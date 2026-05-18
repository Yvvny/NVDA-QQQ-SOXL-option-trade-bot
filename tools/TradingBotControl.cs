using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Windows.Forms;

namespace TradingBotControl
{
    public class MainForm : Form
    {
        private readonly string rootPath;
        private readonly string pythonPath;
        private Process uiProcess;
        private Process paperProcess;
        private ComboBox sourceBox;
        private TextBox symbolBox;
        private NumericUpDown dteBox;
        private NumericUpDown candidateBox;
        private Label uiStatus;
        private Label modeStatus;
        private Label accountStatus;
        private RichTextBox logBox;

        public MainForm()
        {
            rootPath = ResolveRootPath();
            pythonPath = Path.Combine(rootPath, ".venv", "Scripts", "python.exe");

            Text = "Trading Bot Control";
            Width = 900;
            Height = 640;
            MinimumSize = new Size(780, 520);
            StartPosition = FormStartPosition.CenterScreen;

            BuildLayout();
            Log("Root: " + rootPath);
            Log(File.Exists(pythonPath)
                ? "Python found: " + pythonPath
                : "Python not found. Expected: " + pythonPath);
            RefreshStatus();
        }

        private void BuildLayout()
        {
            var main = new TableLayoutPanel();
            main.Dock = DockStyle.Fill;
            main.Padding = new Padding(14);
            main.RowCount = 4;
            main.ColumnCount = 1;
            main.RowStyles.Add(new RowStyle(SizeType.Absolute, 58));
            main.RowStyles.Add(new RowStyle(SizeType.Absolute, 52));
            main.RowStyles.Add(new RowStyle(SizeType.Absolute, 132));
            main.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            Controls.Add(main);

            var title = new Label();
            title.Text = "Trading Bot Control";
            title.Font = new Font("Segoe UI", 16, FontStyle.Bold);
            title.Dock = DockStyle.Fill;
            title.TextAlign = ContentAlignment.MiddleLeft;
            main.Controls.Add(title, 0, 0);

            var statusPanel = new TableLayoutPanel();
            statusPanel.Dock = DockStyle.Fill;
            statusPanel.ColumnCount = 3;
            statusPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33));
            statusPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33));
            statusPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 34));
            main.Controls.Add(statusPanel, 0, 1);

            uiStatus = new Label { Text = "UI server: stopped", Dock = DockStyle.Fill };
            modeStatus = new Label { Text = "Mode: checking", Dock = DockStyle.Fill };
            accountStatus = new Label { Text = "Account: not checked", Dock = DockStyle.Fill };
            statusPanel.Controls.Add(uiStatus, 0, 0);
            statusPanel.Controls.Add(modeStatus, 1, 0);
            statusPanel.Controls.Add(accountStatus, 2, 0);

            var controls = new GroupBox();
            controls.Text = "Controls";
            controls.Dock = DockStyle.Fill;
            main.Controls.Add(controls, 0, 2);

            var grid = new TableLayoutPanel();
            grid.Dock = DockStyle.Fill;
            grid.Padding = new Padding(10);
            grid.ColumnCount = 8;
            grid.RowCount = 3;
            for (int i = 0; i < 8; i++)
            {
                grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 12.5f));
            }
            controls.Controls.Add(grid);

            grid.Controls.Add(new Label { Text = "Source", Dock = DockStyle.Fill }, 0, 0);
            grid.Controls.Add(new Label { Text = "Symbol", Dock = DockStyle.Fill }, 1, 0);
            grid.Controls.Add(new Label { Text = "Target DTE", Dock = DockStyle.Fill }, 2, 0);
            grid.Controls.Add(new Label { Text = "Max Candidates", Dock = DockStyle.Fill }, 3, 0);

            sourceBox = new ComboBox { Dock = DockStyle.Fill, DropDownStyle = ComboBoxStyle.DropDownList };
            sourceBox.Items.AddRange(new object[] { "mock", "tastytrade" });
            sourceBox.SelectedIndex = 0;
            symbolBox = new TextBox { Text = "QQQ", Dock = DockStyle.Fill };
            dteBox = new NumericUpDown { Minimum = 1, Maximum = 365, Value = 30, Dock = DockStyle.Fill };
            candidateBox = new NumericUpDown { Minimum = 1, Maximum = 20, Value = 1, Dock = DockStyle.Fill };
            grid.Controls.Add(sourceBox, 0, 1);
            grid.Controls.Add(symbolBox, 1, 1);
            grid.Controls.Add(dteBox, 2, 1);
            grid.Controls.Add(candidateBox, 3, 1);

            AddButton(grid, "Start UI", 0, StartUi);
            AddButton(grid, "Open UI", 1, OpenUi);
            AddButton(grid, "Stop UI", 2, StopUi);
            AddButton(grid, "Run Once", 3, RunOnce);
            AddButton(grid, "Start Paper", 4, StartPaper);
            AddButton(grid, "Stop Paper", 5, StopPaper);
            AddButton(grid, "Paper Status", 6, PaperStatus);
            AddButton(grid, "Account", 7, CheckAccount);

            var logGroup = new GroupBox();
            logGroup.Text = "Log";
            logGroup.Dock = DockStyle.Fill;
            main.Controls.Add(logGroup, 0, 3);

            logBox = new RichTextBox();
            logBox.Dock = DockStyle.Fill;
            logBox.ReadOnly = true;
            logBox.Font = new Font("Consolas", 10);
            logGroup.Controls.Add(logBox);
        }

        private void AddButton(TableLayoutPanel grid, string text, int column, EventHandler handler)
        {
            var button = new Button();
            button.Text = text;
            button.Dock = DockStyle.Fill;
            button.Click += handler;
            grid.Controls.Add(button, column, 2);
        }

        private void StartUi(object sender, EventArgs e)
        {
            if (uiProcess != null && !uiProcess.HasExited)
            {
                Log("UI server is already running.");
                return;
            }
            uiProcess = StartPython("-m trading_bot ui --host 127.0.0.1 --port 8765", false);
            uiStatus.Text = "UI server: running at http://127.0.0.1:8765";
            Log("Started hidden UI server.");
        }

        private void OpenUi(object sender, EventArgs e)
        {
            if (uiProcess == null || uiProcess.HasExited)
            {
                StartUi(sender, e);
            }
            Process.Start("http://127.0.0.1:8765");
            Log("Opened browser UI.");
        }

        private void StopUi(object sender, EventArgs e)
        {
            if (uiProcess == null || uiProcess.HasExited)
            {
                uiStatus.Text = "UI server: stopped";
                Log("UI server is already stopped.");
                return;
            }
            uiProcess.Kill();
            uiProcess.WaitForExit(3000);
            uiProcess = null;
            uiStatus.Text = "UI server: stopped";
            Log("Stopped UI server.");
        }

        private void RunOnce(object sender, EventArgs e)
        {
            string args = "-m trading_bot run-once --source " + sourceBox.Text
                + " --symbol " + Sanitize(symbolBox.Text)
                + " --target-dte " + dteBox.Value
                + " --max-candidates " + candidateBox.Value;
            RunCaptured("Running one dry-run scan...", args);
        }

        private void StartPaper(object sender, EventArgs e)
        {
            if (paperProcess != null && !paperProcess.HasExited)
            {
                Log("Paper simulator is already running.");
                return;
            }
            string args = "-m trading_bot paper-run --source " + sourceBox.Text
                + " --symbols " + Sanitize(symbolBox.Text)
                + " --target-dte " + dteBox.Value
                + " --max-candidates " + candidateBox.Value
                + " --starting-equity 2000 --cycles 0 --days 30 --interval-seconds 300"
                + " --quote-timeout-seconds 5 --max-contracts 80 --strict-spec";
            paperProcess = StartPython(args, false);
            Log("Started 30-day virtual paper simulator with $2000 equity.");
        }

        private void StopPaper(object sender, EventArgs e)
        {
            if (paperProcess == null || paperProcess.HasExited)
            {
                Log("Paper simulator is already stopped.");
                return;
            }
            paperProcess.Kill();
            paperProcess.WaitForExit(3000);
            paperProcess = null;
            Log("Stopped paper simulator.");
        }

        private void PaperStatus(object sender, EventArgs e)
        {
            RunCaptured("Checking virtual paper account...", "-m trading_bot paper-status");
        }

        private void CheckAccount(object sender, EventArgs e)
        {
            RunCaptured("Checking read-only account...", "-m trading_bot account --summary");
        }

        private void RefreshStatus(object sender, EventArgs e)
        {
            RefreshStatus();
        }

        private void RefreshStatus()
        {
            RunCaptured("Checking bot status...", "-m trading_bot status");
        }

        private void RunCaptured(string startMessage, string args)
        {
            Log(startMessage);
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    string output = RunPythonCapture(args);
                    BeginInvoke(new Action(delegate
                    {
                        Log(output.Trim());
                        if (args.Contains(" status"))
                        {
                            modeStatus.Text = output.Contains("\"mode\": \"dry_run\"")
                                ? "Mode: dry_run"
                                : "Mode: see log";
                        }
                        if (args.Contains(" account"))
                        {
                            accountStatus.Text = output.Contains("\"connected\": true")
                                ? "Account: connected"
                                : "Account: see log";
                        }
                    }));
                }
                catch (Exception ex)
                {
                    BeginInvoke(new Action(delegate { Log("Error: " + ex.Message); }));
                }
            });
        }

        private Process StartPython(string args, bool redirect)
        {
            if (!File.Exists(pythonPath))
            {
                throw new FileNotFoundException("Missing project virtual environment python.exe", pythonPath);
            }
            var start = new ProcessStartInfo();
            start.FileName = pythonPath;
            start.Arguments = args;
            start.WorkingDirectory = rootPath;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            start.RedirectStandardOutput = redirect;
            start.RedirectStandardError = redirect;
            return Process.Start(start);
        }

        private string RunPythonCapture(string args)
        {
            using (var process = StartPython(args, true))
            {
                string output = process.StandardOutput.ReadToEnd();
                string error = process.StandardError.ReadToEnd();
                process.WaitForExit();
                if (process.ExitCode != 0)
                {
                    return output + Environment.NewLine + error;
                }
                return output;
            }
        }

        private void Log(string message)
        {
            if (logBox == null) return;
            logBox.AppendText(DateTime.Now.ToString("HH:mm:ss") + "  " + message + Environment.NewLine);
            logBox.ScrollToCaret();
        }

        private string Sanitize(string value)
        {
            var cleaned = "";
            foreach (char ch in value.ToUpperInvariant())
            {
                if ((ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '.' || ch == '-' || ch == '_')
                {
                    cleaned += ch;
                }
            }
            return cleaned.Length == 0 ? "QQQ" : cleaned;
        }

        private string ResolveRootPath()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            if (File.Exists(Path.Combine(baseDir, "pyproject.toml")) && Directory.Exists(Path.Combine(baseDir, ".venv")))
            {
                return baseDir;
            }
            string parent = Directory.GetParent(baseDir) != null
                ? Directory.GetParent(baseDir).FullName
                : baseDir;
            if (File.Exists(Path.Combine(parent, "pyproject.toml")) && Directory.Exists(Path.Combine(parent, ".venv")))
            {
                return parent;
            }
            return baseDir;
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (uiProcess != null && !uiProcess.HasExited)
            {
                uiProcess.Kill();
            }
            if (paperProcess != null && !paperProcess.HasExited)
            {
                paperProcess.Kill();
            }
            base.OnFormClosing(e);
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
        }
    }
}

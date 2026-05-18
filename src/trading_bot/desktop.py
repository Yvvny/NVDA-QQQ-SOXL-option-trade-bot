from __future__ import annotations

import queue
import socket
import threading
import time
import tkinter as tk
import webbrowser
from http.server import ThreadingHTTPServer
from tkinter import messagebox, ttk
from typing import Any

from trading_bot.api import UiServerConfig, build_ui_server
from trading_bot.broker import fetch_tastytrade_account_snapshot
from trading_bot.config.settings import load_settings
from trading_bot.runner import DryRunBotRunner


class TradingBotDesktopApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Trading Bot Control")
        self.geometry("860x620")
        self.minsize(780, 560)

        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.server_url: str | None = None
        self.scan_thread: threading.Thread | None = None
        self.scan_stop_event = threading.Event()
        self.messages: queue.Queue[str] = queue.Queue()

        self.source_var = tk.StringVar(value="mock")
        self.symbol_var = tk.StringVar(value="QQQ")
        self.dte_var = tk.StringVar(value="30")
        self.max_candidates_var = tk.StringVar(value="1")
        self.interval_var = tk.StringVar(value="60")
        self.server_status_var = tk.StringVar(value="UI server: stopped")
        self.scan_status_var = tk.StringVar(value="Auto scan: stopped")
        self.account_status_var = tk.StringVar(value="Account: not checked")

        self._build_layout()
        self._log_safety_status()
        self.after(200, self._drain_messages)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = ttk.Frame(self, padding=(14, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Trading Bot Control",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Read-only account view. Trading stays dry-run.",
            foreground="#555555",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        status = ttk.Frame(self, padding=(14, 0, 14, 10))
        status.grid(row=1, column=0, sticky="ew")
        status.columnconfigure((0, 1, 2), weight=1)
        ttk.Label(status, textvariable=self.server_status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.scan_status_var).grid(row=0, column=1, sticky="w")
        ttk.Label(status, textvariable=self.account_status_var).grid(row=0, column=2, sticky="w")

        controls = ttk.LabelFrame(self, text="Controls", padding=12)
        controls.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))
        for column in range(6):
            controls.columnconfigure(column, weight=1)

        ttk.Label(controls, text="Source").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.source_var,
            values=("mock", "tastytrade"),
            state="readonly",
            width=14,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(controls, text="Symbol").grid(row=0, column=1, sticky="w")
        ttk.Entry(controls, textvariable=self.symbol_var, width=12).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(0, 8),
        )

        ttk.Label(controls, text="Target DTE").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.dte_var, width=10).grid(
            row=1,
            column=2,
            sticky="ew",
            padx=(0, 8),
        )

        ttk.Label(controls, text="Max candidates").grid(row=0, column=3, sticky="w")
        ttk.Entry(controls, textvariable=self.max_candidates_var, width=10).grid(
            row=1,
            column=3,
            sticky="ew",
            padx=(0, 8),
        )

        ttk.Label(controls, text="Loop seconds").grid(row=0, column=4, sticky="w")
        ttk.Entry(controls, textvariable=self.interval_var, width=10).grid(
            row=1,
            column=4,
            sticky="ew",
            padx=(0, 8),
        )

        button_frame = ttk.Frame(controls)
        button_frame.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(12, 0))
        for column in range(7):
            button_frame.columnconfigure(column, weight=1)

        ttk.Button(button_frame, text="Start UI", command=self.start_ui_server).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 6),
        )
        ttk.Button(button_frame, text="Open UI", command=self.open_ui).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 6),
        )
        ttk.Button(button_frame, text="Stop UI", command=self.stop_ui_server).grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(0, 6),
        )
        ttk.Button(button_frame, text="Run Once", command=self.run_once).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(0, 6),
        )
        ttk.Button(button_frame, text="Start Auto", command=self.start_auto_scan).grid(
            row=0,
            column=4,
            sticky="ew",
            padx=(0, 6),
        )
        ttk.Button(button_frame, text="Stop Auto", command=self.stop_auto_scan).grid(
            row=0,
            column=5,
            sticky="ew",
            padx=(0, 6),
        )
        ttk.Button(button_frame, text="Account", command=self.refresh_account).grid(
            row=0,
            column=6,
            sticky="ew",
        )

        log_frame = ttk.LabelFrame(self, text="Log", padding=12)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 14))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap="word", height=20, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def start_ui_server(self) -> None:
        if self.server is not None:
            self._log("UI server is already running.")
            return

        port = _available_port(8765)
        config = UiServerConfig(host="127.0.0.1", port=port)
        self.server = build_ui_server(config)
        self.server_url = f"http://127.0.0.1:{port}"
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            name="trading-bot-ui-server",
            daemon=True,
        )
        self.server_thread.start()
        self.server_status_var.set(f"UI server: running at {self.server_url}")
        self._log(f"Started local web UI at {self.server_url}")

    def stop_ui_server(self) -> None:
        if self.server is None:
            self._log("UI server is already stopped.")
            return
        self.server.shutdown()
        self.server.server_close()
        if self.server_thread is not None:
            self.server_thread.join(timeout=5)
        self.server = None
        self.server_thread = None
        self.server_url = None
        self.server_status_var.set("UI server: stopped")
        self._log("Stopped local web UI.")

    def open_ui(self) -> None:
        if self.server is None:
            self.start_ui_server()
        if self.server_url:
            webbrowser.open(self.server_url)
            self._log(f"Opened {self.server_url}")

    def run_once(self) -> None:
        self._run_background(self._run_once_worker)

    def start_auto_scan(self) -> None:
        if self.scan_thread is not None and self.scan_thread.is_alive():
            self._log("Auto scan is already running.")
            return
        self.scan_stop_event.clear()
        self.scan_thread = threading.Thread(
            target=self._auto_scan_worker,
            name="trading-bot-auto-scan",
            daemon=True,
        )
        self.scan_thread.start()
        self.scan_status_var.set("Auto scan: running")
        self._log("Started auto dry-run loop.")

    def stop_auto_scan(self) -> None:
        self.scan_stop_event.set()
        self.scan_status_var.set("Auto scan: stopping")
        self._log("Stop requested for auto dry-run loop.")

    def refresh_account(self) -> None:
        self._run_background(self._account_worker)

    def _run_once_worker(self) -> None:
        runner = self._runner()
        result = runner.run_once()
        self._send_log(
            "Dry-run result: "
            f"generated={result.generated_candidates}, "
            f"attempted={result.attempted_candidates}, "
            f"accepted={result.accepted}, rejected={result.rejected}, "
            f"statuses={', '.join(result.statuses)}"
        )

    def _auto_scan_worker(self) -> None:
        cycle = 1
        try:
            while not self.scan_stop_event.is_set():
                runner = self._runner()
                result = runner.run_once(cycle_index=cycle)
                self._send_log(
                    "Auto cycle "
                    f"{cycle}: generated={result.generated_candidates}, "
                    f"accepted={result.accepted}, rejected={result.rejected}"
                )
                cycle += 1
                interval = self._positive_float(self.interval_var.get(), "Loop seconds")
                deadline = time.time() + interval
                while time.time() < deadline and not self.scan_stop_event.is_set():
                    time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001 - UI should surface local task errors.
            self._send_log(f"Auto scan stopped with error: {exc.__class__.__name__}: {exc}")
        finally:
            self.scan_status_var.set("Auto scan: stopped")
            self.scan_stop_event.clear()
            self._send_log("Auto dry-run loop stopped.")

    def _account_worker(self) -> None:
        snapshot = fetch_tastytrade_account_snapshot()
        if snapshot.connected:
            balances = snapshot.balances or {}
            self.account_status_var.set(
                f"Account: connected {snapshot.account_number_masked or ''}"
            )
            self._send_log(
                "Account connected: "
                f"{snapshot.account_number_masked}, "
                f"net_liq={balances.get('net_liquidating_value')}, "
                f"positions={len(snapshot.positions or [])}"
            )
            return
        self.account_status_var.set("Account: not connected")
        self._send_log(
            f"Account error: {snapshot.error_type or 'AccountError'}: {snapshot.message or ''}"
        )

    def _runner(self) -> DryRunBotRunner:
        return DryRunBotRunner(
            settings=load_settings(),
            source=self.source_var.get(),
            symbol=self.symbol_var.get().upper(),
            target_dte=self._positive_int(self.dte_var.get(), "Target DTE"),
            max_candidates_per_cycle=self._positive_int(
                self.max_candidates_var.get(),
                "Max candidates",
            ),
        )

    def _log_safety_status(self) -> None:
        settings = load_settings()
        self._log("Safety mode loaded:")
        self._log(
            "  "
            + str(
                {
                    "mode": settings.risk.default_mode,
                    "live_trading_default_allowed": settings.forbidden.allow_live_trading_default,
                    "allow_0dte": settings.forbidden.allow_0dte,
                    "allow_naked_options": settings.forbidden.allow_naked_options,
                    "allow_market_orders_options": settings.forbidden.allow_market_orders_options,
                }
            )
        )

    def _run_background(self, target: Any) -> None:
        threading.Thread(target=self._safe_worker, args=(target,), daemon=True).start()

    def _safe_worker(self, target: Any) -> None:
        try:
            target()
        except Exception as exc:  # noqa: BLE001 - UI needs to show task failures.
            self._send_log(f"Error: {exc.__class__.__name__}: {exc}")

    def _send_log(self, message: str) -> None:
        self.messages.put(message)

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _drain_messages(self) -> None:
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            self._log(message)
        self.after(200, self._drain_messages)

    def _on_close(self) -> None:
        if self.scan_thread is not None and self.scan_thread.is_alive():
            self.scan_stop_event.set()
        if self.server is not None:
            self.stop_ui_server()
        self.destroy()

    def _positive_int(self, value: str, label: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a positive integer.") from exc
        if parsed <= 0:
            raise ValueError(f"{label} must be positive.")
        return parsed

    def _positive_float(self, value: str, label: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a positive number.") from exc
        if parsed <= 0:
            raise ValueError(f"{label} must be positive.")
        return parsed


def _available_port(preferred: int) -> int:
    if _is_port_available(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def main() -> int:
    try:
        app = TradingBotDesktopApp()
        app.mainloop()
    except Exception as exc:  # noqa: BLE001 - visible fallback for desktop launch failures.
        messagebox.showerror("Trading Bot Control", f"{exc.__class__.__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

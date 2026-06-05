# ChatGPT Strategy Review Prompt - 2026-06-04

Use this prompt with the full contents of:

`docs/engineering/Current_Strategy_Risk_Review_2026-06-04.md`

## Prompt

You are reviewing a paper-trading options bot focused on QQQ, NVDA, SOXL, SPY, IWM, and SMH.

The bot is research-first and paper-first. Live trading must remain disabled unless explicitly gated by human approval and the risk engine. Do not recommend bypassing risk controls, live auto-trading, 0DTE trading, naked options, market orders for options, or automatic LLM-driven strategy changes.

Please review the attached strategy/risk document and answer in Chinese.

Focus on these questions:

1. What is the current effective strategy, not just the strategy the system claims to support?
2. Which parts of the current strategy have the strongest expected edge?
3. Which positions or strategy branches look more experimental than production-ready?
4. Are the current risk limits, sizing rules, and total open risk rules coherent?
5. Is the strategy overexposed to one symbol, one regime, or one trade structure?
6. What are the top 5 concrete improvements, ordered by expected impact and implementation safety?
7. Which improvements should be implemented first in code?
8. Which improvements should only be tested in backtest or paper mode first?
9. What data should be collected to validate the strategy over the next 1-3 months?
10. What should not be changed yet?

Use this output structure:

```text
1. Executive conclusion
2. Current strategy diagnosis
3. Risk and sizing diagnosis
4. Trade timing diagnosis
5. Top improvement priorities
6. Backtest / paper validation plan
7. Things not to change yet
8. Codex-ready implementation tasks
```

For the `Codex-ready implementation tasks` section, write each task as a clear engineering request with:

- target files
- intended behavior
- safety constraints
- required tests
- expected risk impact

Do not assume the bot has a profitable edge unless the provided data supports it. Be explicit about uncertainty.

# ADR 0007: Analysis-depth presets and their precedence

Status: accepted

Context: the underlying TradingAgents graph exposes many cost/quality knobs
(debate round counts, analyst roster, model tiers, news limits, recursion
budget). Exposing all of them in the UI would recreate a settings maze, while
pure environment configuration (`TA_WEBGUI_*` / `TRADINGAGENTS_*`) is invisible
to first-time users. Users think in outcomes — "quick look", "normal run",
"pull out all the stops" — not in individual knobs.

Decision:

- Three named presets, selected per run as ``depth`` on ``RunSpec``:
  ``fast``, ``standard`` (default), ``deep``. Each preset is a declarative
  table in ``server/app/instructions/graph_config.py`` (``DEPTH_PRESETS``)
  mapping to concrete config keys:

  ============================  ==========  ==========  ==========
  Config key                    fast        standard    deep
  ============================  ==========  ==========  ==========
  ``max_debate_rounds``         0           *(unset)*   3
  ``max_risk_discuss_rounds``   0           *(unset)*   2
  ``selected_analysts``         market +    *(unset)*   all four
                                fundamen-               analysts
                                tals only
  ``deep_think_llm``            follows     *(unset)*   *(unset)*
                                quick
                                model
  effort key (provider-         low         *(unset)*   high
  aware, see below)
  ``news_article_limit``        10          *(unset)*   30
  ``global_news_article_limit`` 5           *(unset)*   15
  ``global_news_lookback_days`` 3           *(unset)*   14
  ``max_recur_limit``           *(unset)*   *(unset)*   150
  ============================  ==========  ==========  ==========

  ``standard`` intentionally maps nothing: it is exactly today's behaviour,
  so existing pure-env deployments see zero drift on the default path.

- **Precedence for depth-mapped keys: request depth preset >
  ``TA_WEBGUI_*`` > ``TRADINGAGENTS_*`` > GUI baseline.** A preset is an
  explicit user statement about cost/quality for *this* run, so it outranks
  machine-wide environment defaults on the keys it maps. Keys a preset does
  *not* map keep the existing precedence unchanged (environment still wins
  over baseline). The reasoning-effort keys are provider-aware: the preset
  writes ``low``/``high`` only to the key matching the resolved
  ``llm_provider`` (``openai_reasoning_effort``, ``google_thinking_level``,
  ``anthropic_effort``); other providers keep ``None``.
- ``fast`` collapses ``deep_think_llm`` onto the *resolved* quick model via a
  sentinel applied after environment resolution, so
  ``TA_WEBGUI_QUICK_MODEL`` also shapes the deep-role model instead of
  leaking an expensive deep model into a fast run.
- ``depth`` flows through the single shared resolution path: ``RunSpec`` →
  ``InstructionProvider.build_runner`` → ``resolve_graph_config(depth)`` /
  ``resolve_selected_analysts(depth)`` in both adapters. No adapter keeps its
  own copy of preset logic.
- Validation is total: unknown depth is rejected by the resolver, the job
  manager, and the API (pydantic ``Literal`` → the existing static 422
  envelope, no echo of the rejected value).

Alternatives considered: free-form per-knob UI overrides (rejected: settings
maze, and per-run env mutation is racy); presets applied only when env is
absent (rejected: the user's explicit preset choice losing to an ambient
machine default is surprising); mapping preset keys as new defaults *under*
env (rejected: a user picking "deep" in the UI and silently getting 1 debate
round from env would be a wrong-output bug, not a graceful fallback).

Consequences: run configs are reproducible from ``(ticker, date, depth,
env)``; ``standard`` is regression-free by construction; adding a future
preset is a table row, not code. The trade-off — depth beating
``TA_WEBGUI_*`` on mapped keys — is deliberate and documented in the module
docstring and the user guide.
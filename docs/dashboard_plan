# Crypto Bot Dashboard MVP Plan (Condensed)

## Objective

Build a simple Streamlit dashboard to operate the bot (paper trading +
backtesting) while keeping a clean architecture for future migration.

## Key Principle

Streamlit = UI only. All logic (runs, config, logs, metrics) lives in
reusable service modules.

## Architecture

-   Keep existing bot + CLI
-   Add services layer (run, config, status, logs, metrics)
-   Streamlit calls services only
-   No business logic in UI

## MVP Features

-   Start/stop paper trading
-   Launch backtests
-   Select/edit configs (safe fields only)
-   View status + kill switch
-   View logs
-   View trades/orders
-   View metrics (PnL, equity)
-   Live trading disabled

## Constraints

-   Do not break CLI
-   Do not expose secrets
-   Do not bypass risk manager
-   Keep implementation simple
-   No overengineering

## Implementation Steps

1.  Extract logic from CLI into services\
2.  Build service layer\
3.  Update CLI to use services\
4.  Build Streamlit UI\
5.  Add basic tests\
6.  Update README

## Future Path

-   Replace Streamlit with FastAPI + React OR desktop app\
-   Reuse service layer entirely

## Acceptance Criteria

-   Can operate bot without terminal\
-   CLI still works\
-   UI is thin\
-   Services reusable\
-   Safety controls intact

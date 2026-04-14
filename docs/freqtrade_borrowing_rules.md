# Freqtrade Borrowing Rules

## Purpose

This project may borrow useful ideas and patterns from Freqtrade, but should not be converted into a copy of Freqtrade or rebuilt around Freqtrade’s full framework.

The goal is to selectively borrow proven concepts that improve this bot’s research quality, strategy development process, and operational discipline.

## What to borrow

Borrow ideas such as:
- strategy structure patterns
- cleaner entry / exit separation
- confirmation filters
- protections and guardrails
- parameter organization
- backtesting discipline
- walk-forward / validation mindset
- artifact/reporting ideas
- useful workflow patterns for reproducible research
- multi-symbol evaluation concepts
- realistic fee/slippage awareness

## What not to borrow blindly

Do not borrow:
- the entire framework architecture
- unnecessary abstractions
- framework-specific complexity that does not fit this repo
- parameter explosion / optimization-heavy workflows
- any feature just because it exists in Freqtrade
- strategy logic without validating whether it fits crypto, symbol, timeframe, and project goals

## Rule for adopting ideas

An idea borrowed from Freqtrade should only be adopted if it clearly improves one or more of these:
- signal quality
- validation quality
- reproducibility
- risk management
- maintainability
- portfolio-level evaluation

If an idea mainly adds complexity without solving a real problem in this repo, do not adopt it.

## Current interpretation

At the current stage, borrowing should focus on:
- research discipline
- clean strategy modularity
- protections / guardrails
- multi-symbol evaluation logic
- reporting and validation workflows

At the current stage, borrowing should not focus on:
- replacing the repo’s core architecture
- recreating Freqtrade internals
- expanding optimization complexity before strategy edge is proven

## Current project alignment

This repo should continue as its own system.

The current plan is:
- keep the framework
- improve the strategy layer
- test ORB as the next day-mode candidate
- keep 4h SMA parked as a swing-mode candidate
- revisit multi-symbol expansion after a real strategy edge appears

Freqtrade ideas should support that direction, not override it.
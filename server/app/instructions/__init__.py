"""Instruction layer: providers that turn run parameters into graph runners.

Only this package may import ``ta_plugins`` (ADR 0002); the engine and API
layers depend exclusively on ``app.instructions.interface``.
"""

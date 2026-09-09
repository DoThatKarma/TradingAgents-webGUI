# ADR 0002: Decoupled instruction layer (plugin framework optional)

Status: accepted

The engine depends only on an `InstructionProvider` protocol. `ta_plugins` is
one adapter; a `direct` adapter (no custom instructions) is the fallback.
Rationale: if the plugin framework proves unviable, the GUI continues working
with only small changes (owner directive).

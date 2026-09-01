"""Static lint commands for echo-agent.

Command modules are intentionally not imported here: eager imports make
``python -m tools.lint.<command>`` execute a module that is already present in
``sys.modules``, producing a runtime warning and risking duplicate state.
"""

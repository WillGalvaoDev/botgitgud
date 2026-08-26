"""Operational tooling that lives outside `bot/` and `analysis/` on purpose:
process supervision (`supervisor.py`) is an OS-process concern, not a Discord
or analysis concern. Nothing here calls the WCL or Discord.
"""

"""The persona's mind: an opt-in process the companion starts when the persona is on.

It reads the daemon's allowlisted events from stdin, notices moments worth reacting
to, keeps her mood and memory as files in the app data folder, and speaks through the
daemon's POST /say. voice.py is the only module that calls the network, and only the
Anthropic API.
"""

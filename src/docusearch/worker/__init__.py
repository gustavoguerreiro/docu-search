"""The background worker: runs RQ jobs in a separate process from the API.

Entry point: `python -m docusearch.worker`. It uses the same image and the same domain code as
the API.
"""

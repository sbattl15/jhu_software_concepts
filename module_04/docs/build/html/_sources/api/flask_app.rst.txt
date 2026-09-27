app -- Flask Application & Routes
=================================

``src/flask_website/app.py``

.. automodule:: app
   :no-members:

HTTP routes
-----------

.. autofunction:: app.analysis
.. autofunction:: app.pull_data
.. autofunction:: app.pull_data_status
.. autofunction:: app.update_analysis

Pull Data control
-----------------

.. autofunction:: app.start_pull_data
.. autofunction:: app._watch_pull_process
.. autofunction:: app._pull_is_running
.. autofunction:: app._pull_status_snapshot

Analysis helpers
----------------

.. autofunction:: app._build_analysis_context
.. autofunction:: app._fmt_pct
.. autofunction:: app._iso

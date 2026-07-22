"""Adapters package (`FlowDriver`, `SystemSampler`, `MarkerSource`, `Store`,
`RunContextProvider`, `Clock` implementations). PR2 store-half shipped
`store_sqlite.py`; PR2 adapters-half adds `sampler_flashlight.py`,
`markers_adb_logcat.py`, `driver_maestro.py`, `driver_manual.py`,
`context_bash_perfmeta.py`, the shared non-port `process.py` spawn helper,
and `registry.py` (name-to-factory maps, design §6).
"""

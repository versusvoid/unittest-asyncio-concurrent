# unittest-asyncio-concurrent

POC concurrent (in event loop) execution of your TestCases. With support for contextual `unittest.mock`.
Allows all and any `*setUp()`, `*tearDown()` and test being async functions.

Missing support for a lot details not required for POC:
- `doClassCleanups()`/`doModuleCleanups()` because it would require more copy-paste from `unittest`
- `patch.dict()` (but can be added in like 5 minutes).
- `debug()`
- output capture
- ?????

Usage:
```sh
$ python -m uac.unittest *usual unittest arguments*
```
or (will replace `unittest.defaultTestLoader`):
```python
import uac.unittest
```

For example see [test of test](tests/test_test.py).

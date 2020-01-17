# Reuses some code from cpython library.
# Copyright © 2001-2020 Python Software Foundation; All Rights Reserved

from typing import List, Tuple, Iterable
import asyncio
import collections
import collections.abc
import contextlib
import contextvars
import functools
import inspect
import sys
import unittest
import unittest.mock


_context_entered_mocks = contextvars.ContextVar('_entered_unittest_mocks')
_context_active_mocks = contextvars.ContextVar('_active_unittest_mocks')
_global_entered_mocks = []

_original_enter = unittest.mock._patch.__enter__
_original_exit = unittest.mock._patch.__exit__

# TODO patch.dict

@functools.wraps(unittest.mock._patch.__enter__)
def _first_patch_enter(self, loop):
    # Adding mocks context managing overhead only if tests actually use mocks
    loop.call_soon = functools.partial(loop.call_soon, _active_mocks_contextmanager)
    unittest.mock._patch.__enter__ = _patch_enter
    return _patch_enter(self)


# Keeping track of global mocking state
@functools.wraps(unittest.mock._patch.__enter__)
def _patch_enter(self):
    mock = _original_enter(self)
    _global_entered_mocks.append((self, mock))
    return mock


@functools.wraps(unittest.mock._patch.__exit__)
def _patch_exit(self, *args):
    _original_exit(self, *args)
    for i, (patcher, _) in enumerate(reversed(_global_entered_mocks)):
        if patcher is self:
            _global_entered_mocks.pop(len(_global_entered_mocks) - i - 1)
            break


def _record_mocks():
    # Recording global mocking state into context
    _context_active_mocks.set(unittest.mock._patch._active_patches[:])
    _context_entered_mocks.set(_global_entered_mocks[:])


def _swap_mocks_context(new_entered: List[tuple], new_active: list):
    for patch, mock in _global_entered_mocks:
        # Forcing patch to reuse the same mock on next activation (context switch)
        patch.autospec = None
        patch.kwargs = None
        patch.new = mock
        _original_exit(patch)

    previous_entered = _global_entered_mocks[:]
    previous_active = unittest.mock._patch._active_patches[:]

    _global_entered_mocks[:] = new_entered
    unittest.mock._patch._active_patches[:] = new_active

    for patch, mock in _global_entered_mocks:
        mock2 = _original_enter(patch)
        # Sanity check
        assert mock is mock2

    return previous_entered, previous_active


def _active_mocks_contextmanager(callback, *args):
    previous = _swap_mocks_context(_context_entered_mocks.get(()), _context_active_mocks.get(()))

    try:
        callback(*args)
    finally:
        _record_mocks()
        _swap_mocks_context(*previous)


async def maybe_await(f, *args, **kwargs):
    # Why this isn't in standard library?
    if f is None:
        return None

    result = f(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    else:
        return result


# TODO addCleanup/doCleanups

class ParallelAsyncioTestCaseMixin(object):
    # copy-paste from TestCase/IsolatedAsyncioTestCase cpython 3.8.1
    async def asyncSetUp(self):
        pass

    async def asyncTearDown(self):
        pass

    def addAsyncCleanup(self, func, /, *args, **kwargs):
        self.addCleanup(*(func, *args), **kwargs)

    async def run(self, result=None):
        # Mostly copy-paste from TestCase
        # allowing for async setUp(), testMethod, tearDown(), cleanup functions

        orig_result = result
        if result is None:
            result = self.defaultTestResult()
            startTestRun = getattr(result, 'startTestRun', None)
            if startTestRun is not None:
                startTestRun()

        result.startTest(self)

        testMethod = getattr(self, self._testMethodName)
        if (getattr(self.__class__, "__unittest_skip__", False) or
            getattr(testMethod, "__unittest_skip__", False)):
            # If the class or method was skipped.
            try:
                skip_why = (getattr(self.__class__, '__unittest_skip_why__', '')
                            or getattr(testMethod, '__unittest_skip_why__', ''))
                self._addSkip(result, self, skip_why)
            finally:
                result.stopTest(self)
            return
        expecting_failure_method = getattr(testMethod,
                                           "__unittest_expecting_failure__", False)
        expecting_failure_class = getattr(self,
                                          "__unittest_expecting_failure__", False)
        expecting_failure = expecting_failure_class or expecting_failure_method
        outcome = unittest.case._Outcome(result)
        try:
            self._outcome = outcome

            with outcome.testPartExecutor(self):
                await maybe_await(self.setUp)
                await maybe_await(self.asyncSetUp)

            if outcome.success:
                outcome.expecting_failure = expecting_failure
                with outcome.testPartExecutor(self, isTest=True):
                    await maybe_await(testMethod)

                outcome.expecting_failure = False
                with outcome.testPartExecutor(self):
                    await maybe_await(self.tearDown)
                    await maybe_await(self.asyncTearDown)

            await self._async_do_cleanups()
            for test, reason in outcome.skipped:
                self._addSkip(result, test, reason)

            self._feedErrorsToResult(result, outcome.errors)
            if outcome.success:
                if expecting_failure:
                    if outcome.expectedFailure:
                        self._addExpectedFailure(result, outcome.expectedFailure)
                    else:
                        self._addUnexpectedSuccess(result)
                else:
                    result.addSuccess(self)
            return result
        finally:
            result.stopTest(self)
            if orig_result is None:
                stopTestRun = getattr(result, 'stopTestRun', None)
                if stopTestRun is not None:
                    stopTestRun()

            # explicitly break reference cycles:
            # outcome.errors -> frame -> outcome -> outcome.errors
            # outcome.expectedFailure -> frame -> outcome -> outcome.expectedFailure
            outcome.errors.clear()
            outcome.expectedFailure = None

            # clear the outcome, no more needed
            self._outcome = None

    async def _async_do_cleanups(self):
        while self._cleanups:
            function, args, kwargs = self._cleanups.pop()
            with self._outcome.testPartExecutor(self):
                await maybe_await(function, *args, **kwargs)


class ParallelAsyncioTestSuite(unittest.BaseTestSuite):

    TestModule = collections.namedtuple('TestModule', 'submodules, classes')

    def __init__(self):
        self._tree = ParallelAsyncioTestSuite.TestModule({}, {})

    def add_to_tree(self, tests: Iterable[unittest.TestCase]):
        tests = list(tests)
        if not tests:
            return

        assert isinstance(tests[0], unittest.TestCase)
        assert all(t.__class__ is tests[0].__class__ for t in tests)
        # 0 - MixedTestCaseClass
        # 1 - ParallelAsyncioTestCaseMixin
        # 2 - original testCaseClass
        original_class = inspect.getmro(tests[0].__class__)[2]

        module_components = original_class.__module__.split('.')
        current = self._tree
        for i, component in enumerate(module_components):
            if component not in current.submodules:
                new = ParallelAsyncioTestSuite.TestModule({}, collections.defaultdict(list))
                current.submodules[component] = new
                current = new
            else:
                current = current.submodules[component]

        current.classes[original_class.__name__].extend(tests)

    def run(self, result, debug=False):
        self.loop = asyncio.new_event_loop()

        unittest.mock._patch.__enter__ = functools.partialmethod(_first_patch_enter, self.loop)
        unittest.mock._patch.__exit__ = _patch_exit
        try:
            self.loop.run_until_complete(self._run_module((), self._tree, result, debug))
        finally:
            unittest.mock._patch.__enter__ = _original_enter
            unittest.mock._patch.__exit__ = _original_exit

    async def _run_module(self, module_name: Tuple[str], tests: TestModule, result, debug):
        async with self._module_fixture_contextmanager('.'.join(module_name), result, debug):
            await asyncio.gather(
                *(
                    asyncio.create_task(self._run_class(c, result, debug))
                    for c in tests.classes.values()
                ),
                *(
                    asyncio.create_task(self._run_module((*module_name, k), m, result, debug))
                    for k, m in tests.submodules.items()
                ),
            )

    async def _run_class(self, tests: List[unittest.TestCase], result, debug):
        if not tests:
            return

        async with self._class_fixture_contextmanager(tests[0].__class__, result, debug):
            await asyncio.gather(
                *(
                    asyncio.create_task(maybe_await(t, result))
                    for t in tests
                ),
            )

    # Partial copy-pastes from TestSuite
    @contextlib.asynccontextmanager
    async def _module_fixture_contextmanager(self, module_name, result, debug):
        setUpModule = None
        tearDownModule = None
        try:
            module = sys.modules[module_name]
            setUpModule = getattr(module, 'setUpModule', None)
            tearDownModule = getattr(module, 'tearDownModule', None)
        except KeyError:
            pass

        if setUpModule is not None:
            try:
                await maybe_await(setUpModule)
            except Exception as e:
                if debug:
                    raise
                self._createClassOrModuleLevelException(result, e, 'setUpModule', module_name)
                return

        yield

        if tearDownModule is not None:
            try:
                await maybe_await(tearDownModule)
            except Exception as e:
                if debug:
                    raise
                self._createClassOrModuleLevelException(result, e, 'tearDownModule', module_name)

    @contextlib.asynccontextmanager
    async def _class_fixture_contextmanager(self, cls, result, debug):
        setUpClass = getattr(cls, 'setUpClass', None)
        if setUpClass is not None:
            try:
                await maybe_await(setUpClass)
            except Exception as e:
                if debug:
                    raise
                self._createClassOrModuleLevelException(result, e, 'setUpClass', str(cls))
                return

        yield

        tearDownClass = getattr(cls, 'tearDownClass', None)
        if tearDownClass is not None:
            try:
                await maybe_await(tearDownClass)
            except Exception as e:
                if debug:
                    raise
                self._createClassOrModuleLevelException(result, e, 'tearDownClass', str(cls))


class ParallelAsyncioTestLoader(unittest.TestLoader):
    # Simple wrapper which reuses single suite and mixes in async run() for test cases

    def __init__(self):
        super().__init__()
        self._suite = ParallelAsyncioTestSuite()

    def loadTestsFromTestCase(self, testCaseClass):
        class MixedTestCaseClass(ParallelAsyncioTestCaseMixin, testCaseClass):
            pass

        return super().loadTestsFromTestCase(MixedTestCaseClass)

    def suiteClass(self, tests):
        self._suite.add_to_tree(testCase for testCase in tests if testCase is not self._suite)
        return self._suite


def main(**kwargs):
    unittest.main(testLoader=ParallelAsyncioTestLoader(), **kwargs)


if __name__ == '__main__':
    main(module=None)
else:
    unittest.defaulTestLoader = ParallelAsyncioTestLoader()

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


class ContextMockingWrapperPolicy(asyncio.AbstractEventLoopPolicy):  # FIXME not needed, patch directly in Suite.run

    def __new__(cls, wrapped=None):
        wrapped = wrapped or asyncio.get_event_loop_policy()
        if not isinstance(wrapped.new_event_loop, functools.partial):
            wrapped.new_event_loop = functools.partial(_new_event_loop_with_patcher, wrapped.new_event_loop)

        return wrapped


def _new_event_loop_with_patcher(original_new_event_loop):  # FIXME not needed, patch directly in Suite.run
    print('_new_event_loop_with_patcher')
    loop = original_new_event_loop()

    assert not isinstance(loop.call_soon, functools.partial)
    loop.call_soon = functools.partial(_call_soon_with_mocks, loop.call_soon)

    return loop


def _call_soon_with_mocks(original_call_soon, callback, *args, context=None):
    if context is None:
        context = contextvars.copy_context()
    else:
        context = context.copy()

    context.run(_record_active_mocks)
    return original_call_soon(_active_mocks_contextmanager, callback, *args, context=context)


active_mocks = contextvars.ContextVar('_active_unittest_mocks')


def _record_active_mocks():
    active_mocks.set(unittest.mock._patch._active_patches[:])


def _active_mocks_contextmanager(callback, *args):
    current_mocks = unittest.mock._patch._active_patches[:]
    unittest.mock.patch.stopall()
    for p in active_mocks.get():
        p.start()

    print('switched mocks from', current_mocks, 'to', unittest.mock._patch._active_patches)

    try:
        callback(*args)
    finally:
        _record_active_mocks()
        unittest.mock.patch.stopall()
        for p in current_mocks:
            p.start()


async def maybe_await(f, *args, **kwargs):
    if f is None:
        return None

    result = f(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    else:
        return result


# TODO addCleanup/doCleanups
# TODO debug()


class ParallelAsyncioTestCaseMixin(object):
    # copy-paste from TestCase/IsolatedAsyncioTestCase cpython 3.8.1
    async def asyncSetUp(self):
        pass

    async def asyncTearDown(self):
        pass

    #def addAsyncCleanup(self, func, /, *args, **kwargs):
    #    self.addCleanup(*(func, *args), **kwargs)

    async def run(self, result=None):
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

            self.doCleanups()
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
        # 1 - ParallelAsyncioTestSuite
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
        asyncio.set_event_loop_policy(ContextMockingWrapperPolicy())
        asyncio.new_event_loop().run_until_complete(self._run_module((), self._tree, result, debug))

    async def _run_module(self, module_name: Tuple[str], tests: TestModule, result, debug):
        async with self._module_fixture_contextmanager('.'.join(module_name), result, debug):
            await asyncio.gather(
                *(self._run_class(c, result, debug) for c in tests.classes.values()),
                *(self._run_module((*module_name, k), m, result, debug) for k, m in tests.submodules.items()),
            )

    async def _run_class(self, tests: List[unittest.TestCase], result, debug):
        if not tests:
            return

        async with self._class_fixture_contextmanager(tests[0].__class__, result, debug):
            await asyncio.gather(*(maybe_await(t, result) for t in tests))

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

    def __repr__(self):
        return 'ParallelAsyncioTestSuite<>'

class ParallelAsyncioTestLoader(unittest.TestLoader):

    def __init__(self):
        super().__init__()
        self._suite = ParallelAsyncioTestSuite()

    def loadTestsFromTestCase(self, testCaseClass):
        class MixedTestCaseClass(ParallelAsyncioTestCaseMixin, testCaseClass):
            pass

        return super().loadTestsFromTestCase(MixedTestCaseClass)

    def suiteClass(self, tests):
        print('suiteClass', tests)
        self._suite.add_to_tree(testCase for testCase in tests if testCase is not self._suite)
        return self._suite


def main(**kwargs):
    unittest.main(testLoader=ParallelAsyncioTestLoader(), exit=False, **kwargs)
    #import pprint; pprint.pprint(sys.modules)


if __name__ == '__main__':
    main(module=None)

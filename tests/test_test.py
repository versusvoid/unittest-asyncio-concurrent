import os
import unittest
import unittest.mock
import asyncio
import contextvars

import tests.targets


class T(unittest.TestCase):

    @classmethod
    async def setUpClass(cls):
        cls.event1 = asyncio.Event()
        cls.event2 = asyncio.Event()

    async def setUp(self):
        # Shared code between tests for mock creation
        p = unittest.mock.patch('tests.targets.f2', return_value='f2 patched')
        self.addCleanup(p.stop)  # not actually required as mocks live in context

        # Since we have two __concurrent__ mock objects, we need to store them in contextvars
        self.f2_mock = contextvars.ContextVar('f2mock')
        self.f2_mock.set(p.start())

    async def test_thread1(self):
        print('thread1 start')
        # Mock visible to this test only
        p = unittest.mock.patch('tests.targets.f1', return_value='f1 patched')
        self.addCleanup(p.stop)  # not actually required as mocks live in context
        mock = p.start()

        self.assertEqual(tests.targets.f2(), 'f2 patched')

        await self.event1.wait()
        self.event1.clear()
        self.event2.set()
        print('thread1 after sync 1')

        # Due to synchronization thread2 already called it's version of self.f2_mock
        # but it doesn't affect us
        self.f2_mock.get().assert_called_once()
        # Due to synchronization thread2 already patched f3,
        # but it doesn't affect us
        self.assertEqual(tests.targets.f3(), 'f3')

        await self.event1.wait()
        self.event1.clear()
        self.event2.set()
        print('thread1 after sync 2')

        # Due to synchronization thread2 already called (real) f1,
        # but it doesn't affect us
        mock.assert_not_called()

    async def test_thread2(self):
        print('thread2 start')
        p = unittest.mock.patch('tests.targets.f3', return_value='f3 patched')
        # self.addCleanup(p.stop)  # not actually required as mocks live in context
        mock = p.start()

        self.assertEqual(tests.targets.f2(), 'f2 patched')

        self.event1.set()
        await self.event2.wait()
        self.event2.clear()
        print('thread2 after sync 1')

        self.f2_mock.get().assert_called_once()
        self.assertEqual(tests.targets.f1(), 'f1')

        self.event1.set()
        await self.event2.wait()
        self.event2.clear()
        print('thread2 after sync 2')

        mock.assert_not_called()

    def test_thread3_syn(self):
        # Sync test are allowed, are also executed in contexts,
        # but they will simply block loop
        print('thread3 start')
        self.assertEqual(tests.targets.f2(), 'f2 patched')
        print('thread3 end')

    @unittest.skipUnless(os.getenv('TEST_FAIL'), 'Will fail')
    async def test_fail(self):
        self.fail()

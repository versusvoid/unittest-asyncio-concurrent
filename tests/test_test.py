import unittest
import unittest.mock
import asyncio
import contextvars
import traceback

import tests.targets


class T(unittest.TestCase):

    @classmethod
    async def setUpClass(cls):
        cls.event1 = asyncio.Event()
        cls.event2 = asyncio.Event()

    async def setUp(self):
        p = unittest.mock.patch('tests.targets.f2', return_value='f2 patched')
        self.addCleanup(p.stop)
        self.f2_mock = contextvars.ContextVar('f2mock')
        self.f2_mock.set(p.start())

    async def test_thread1(self):
        try:
            p = unittest.mock.patch('tests.targets.f1', return_value='f1 patched')
            self.addCleanup(p.stop)
            mock = p.start()

            self.assertEqual(tests.targets.f2(), 'f2 patched')

            await self.event1.wait()

            self.f2_mock.get().assert_called_once()
            self.assertEqual(tests.targets.f3(), 'f3')

            await self.event2.wait()

            mock.assert_not_called()
        except:
            traceback.print_exc()
            raise

    async def test_thread2(self):
        try:
            p = unittest.mock.patch('tests.targets.f3', return_value='f3 patched')
            self.addCleanup(p.stop)
            mock = p.start()

            self.assertEqual(tests.targets.f2(), 'f2 patched')

            self.event1.set()

            self.f2_mock.get().assert_called_once()
            self.assertEqual(tests.targets.f1(), 'f1')

            self.event2.set()

            mock.assert_not_called()
        except:
            traceback.print_exc()
            raise

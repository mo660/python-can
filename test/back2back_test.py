#!/usr/bin/env python
# coding: utf-8

"""
This module tests two virtual buses attached to each other.
"""

import unittest
from time import sleep
from multiprocessing.dummy import Pool as ThreadPool
import random

import pytest

import can
from can.interfaces.multicast_ip import MulticastIpBus

from .config import IS_CI, IS_UNIX, IS_TRAVIS, TEST_INTERFACE_SOCKETCAN, TEST_CAN_FD


class Back2BackTestCase(unittest.TestCase):
    """Use two interfaces connected to the same CAN bus and test them against each other.

    This very class declaration runs the test on the *virtual* interface but subclasses can be created for
    other buses.
    """

    BITRATE = 500000
    TIMEOUT = 0.1

    INTERFACE_1 = "virtual"
    CHANNEL_1 = "virtual_channel_0"
    INTERFACE_2 = "virtual"
    CHANNEL_2 = "virtual_channel_0"

    def setUp(self):
        self.bus1 = can.Bus(
            channel=self.CHANNEL_1,
            bustype=self.INTERFACE_1,
            bitrate=self.BITRATE,
            fd=TEST_CAN_FD,
            single_handle=True,
        )
        self.bus2 = can.Bus(
            channel=self.CHANNEL_2,
            bustype=self.INTERFACE_2,
            bitrate=self.BITRATE,
            fd=TEST_CAN_FD,
            single_handle=True,
        )

    def tearDown(self):
        self.bus1.shutdown()
        self.bus2.shutdown()

    def _check_received_message(self, recv_msg: can.Message, sent_msg: can.Message) -> None:
        self.assertIsNotNone(
            recv_msg, "No message was received on %s" % self.INTERFACE_2
        )
        self.assertEqual(recv_msg.arbitration_id, sent_msg.arbitration_id)
        self.assertEqual(recv_msg.is_extended_id, sent_msg.is_extended_id)
        self.assertEqual(recv_msg.is_remote_frame, sent_msg.is_remote_frame)
        self.assertEqual(recv_msg.is_error_frame, sent_msg.is_error_frame)
        self.assertEqual(recv_msg.is_fd, sent_msg.is_fd)
        self.assertEqual(recv_msg.bitrate_switch, sent_msg.bitrate_switch)
        self.assertEqual(recv_msg.dlc, sent_msg.dlc)
        if not sent_msg.is_remote_frame:
            self.assertSequenceEqual(recv_msg.data, sent_msg.data)

    def _send_and_receive(self, msg: can.Message) -> None:
        # Send with bus 1, receive with bus 2
        self.bus1.send(msg)
        recv_msg = self.bus2.recv(self.TIMEOUT)
        self._check_received_message(recv_msg, msg)
        # Some buses may receive their own messages. Remove it from the queue
        self.bus1.recv(0)

        # Send with bus 2, receive with bus 1
        # Add 1 to arbitration ID to make it a different message
        msg.arbitration_id += 1
        self.bus2.send(msg)
        recv_msg = self.bus1.recv(self.TIMEOUT)
        self._check_received_message(recv_msg, msg)

    def test_no_message(self):
        self.assertIsNone(self.bus1.recv(0.1))

    @unittest.skipIf(
        IS_CI,
        "the timing sensitive behaviour cannot be reproduced reliably on a CI server",
    )
    def test_timestamp(self):
        self.bus2.send(can.Message())
        recv_msg1 = self.bus1.recv(self.TIMEOUT)
        sleep(2.0)
        self.bus2.send(can.Message())
        recv_msg2 = self.bus1.recv(self.TIMEOUT)
        delta_time = recv_msg2.timestamp - recv_msg1.timestamp
        self.assertTrue(
            1.75 <= delta_time <= 2.25,
            "Time difference should have been 2s +/- 250ms."
            "But measured {}".format(delta_time),
        )

    def test_standard_message(self):
        msg = can.Message(
            is_extended_id=False, arbitration_id=0x100, data=[1, 2, 3, 4, 5, 6, 7, 8]
        )
        self._send_and_receive(msg)

    def test_extended_message(self):
        msg = can.Message(
            is_extended_id=True,
            arbitration_id=0x123456,
            data=[10, 11, 12, 13, 14, 15, 16, 17],
        )
        self._send_and_receive(msg)

    def test_remote_message(self):
        msg = can.Message(
            is_extended_id=False, arbitration_id=0x200, is_remote_frame=True, dlc=4
        )
        self._send_and_receive(msg)

    def test_dlc_less_than_eight(self):
        msg = can.Message(is_extended_id=False, arbitration_id=0x300, data=[4, 5, 6])
        self._send_and_receive(msg)

    @unittest.skip(
        "TODO: how shall this be treated if sending messages locally? should be done uniformly"
    )
    def test_message_is_rx(self):
        """Verify that received messages have is_rx set to `False` while messages
        received on the other virtual interfaces have is_rx set to `True`.
        """
        msg = can.Message(
            is_extended_id=False, arbitration_id=0x300, data=[2, 1, 3], is_rx=False
        )
        self.bus1.send(msg)
        self_recv_msg = self.bus2.recv(self.TIMEOUT)
        self.assertIsNotNone(self_recv_msg)
        self.assertTrue(self_recv_msg.is_rx)

    @unittest.skip(
        "TODO: how shall this be treated if sending messages locally? should be done uniformly"
    )
    def test_message_is_rx_receive_own_messages(self):
        """The same as `test_message_direction` but testing with `receive_own_messages=True`."""
        bus3 = can.Bus(
            channel=self.CHANNEL_2,
            bustype=self.INTERFACE_2,
            bitrate=self.BITRATE,
            fd=TEST_CAN_FD,
            single_handle=True,
            receive_own_messages=True,
        )
        try:
            msg = can.Message(
                is_extended_id=False, arbitration_id=0x300, data=[2, 1, 3], is_rx=False
            )
            bus3.send(msg)
            self_recv_msg_bus3 = bus3.recv(self.TIMEOUT)
            self.assertTrue(self_recv_msg_bus3.is_rx)
        finally:
            bus3.shutdown()

    def test_unique_message_instances(self):
        """Verify that we have a different instances of message for each bus even with
        `receive_own_messages=True`.
        """
        bus3 = can.Bus(
            channel=self.CHANNEL_2,
            bustype=self.INTERFACE_2,
            bitrate=self.BITRATE,
            fd=TEST_CAN_FD,
            single_handle=True,
            receive_own_messages=True,
        )
        try:
            msg = can.Message(
                is_extended_id=False, arbitration_id=0x300, data=[2, 1, 3]
            )
            bus3.send(msg)
            recv_msg_bus1 = self.bus1.recv(self.TIMEOUT)
            recv_msg_bus2 = self.bus2.recv(self.TIMEOUT)
            self_recv_msg_bus3 = bus3.recv(self.TIMEOUT)

            self._check_received_message(recv_msg_bus1, recv_msg_bus2)
            self._check_received_message(recv_msg_bus2, self_recv_msg_bus3)

            recv_msg_bus1.data[0] = 4
            self.assertNotEqual(recv_msg_bus1.data, recv_msg_bus2.data)
            self.assertEqual(recv_msg_bus2.data, self_recv_msg_bus3.data)
        finally:
            bus3.shutdown()

    def test_fd_message(self):
        msg = can.Message(
            is_fd=True, is_extended_id=True, arbitration_id=0x56789, data=[0xFF] * 64
        )
        self._send_and_receive(msg)

    def test_fd_message_with_brs(self):
        msg = can.Message(
            is_fd=True,
            bitrate_switch=True,
            is_extended_id=True,
            arbitration_id=0x98765,
            data=[0xFF] * 48,
        )
        self._send_and_receive(msg)


@unittest.skipUnless(TEST_INTERFACE_SOCKETCAN, "skip testing of socketcan")
class BasicTestSocketCan(Back2BackTestCase):

    INTERFACE_1 = "socketcan"
    CHANNEL_1 = "vcan0"
    INTERFACE_2 = "socketcan"
    CHANNEL_2 = "vcan0"


@unittest.skipUnless(IS_UNIX, "only supported on Unix systems")
class BasicTestInterprocessVirtualBusIPv4(Back2BackTestCase):

    INTERFACE_1 = "multicast_ip"
    CHANNEL_1 = MulticastIpBus.DEFAULT_GROUP_IPv4
    INTERFACE_2 = "multicast_ip"
    CHANNEL_2 = MulticastIpBus.DEFAULT_GROUP_IPv4

    def test_unique_message_instances(self):
        with self.assertRaises(NotImplementedError):
            super().test_unique_message_instances()


@unittest.skipUnless(IS_UNIX, "only supported on Unix systems")
class BasicTestInterprocessVirtualBusIPv6(Back2BackTestCase):

    # Use an "Admin-local" IPv6 multicast address on Travis CI to make it work
    _MULTICAST_IPv6_ADDRESS = "ff14:7079:7468:6f6e:6465:6d6f:6d63:6173" if IS_TRAVIS \
        else MulticastIpBus.DEFAULT_GROUP_IPv6

    INTERFACE_1 = "multicast_ip"
    CHANNEL_1 = _MULTICAST_IPv6_ADDRESS
    INTERFACE_2 = "multicast_ip"
    CHANNEL_2 = _MULTICAST_IPv6_ADDRESS

    def test_unique_message_instances(self):
        with self.assertRaises(NotImplementedError):
            super().test_unique_message_instances()


@unittest.skipUnless(TEST_INTERFACE_SOCKETCAN, "skip testing of socketcan")
class SocketCanBroadcastChannel(unittest.TestCase):
    def setUp(self):
        self.broadcast_bus = can.Bus(channel="", bustype="socketcan")
        self.regular_bus = can.Bus(channel="vcan0", bustype="socketcan")

    def tearDown(self):
        self.broadcast_bus.shutdown()
        self.regular_bus.shutdown()

    def test_broadcast_channel(self):
        self.broadcast_bus.send(can.Message(channel="vcan0"))
        recv_msg = self.regular_bus.recv(1)
        self.assertIsNotNone(recv_msg)
        self.assertEqual(recv_msg.channel, "vcan0")

        self.regular_bus.send(can.Message())
        recv_msg = self.broadcast_bus.recv(1)
        self.assertIsNotNone(recv_msg)
        self.assertEqual(recv_msg.channel, "vcan0")


class TestThreadSafeBus(Back2BackTestCase):
    def setUp(self):
        self.bus1 = can.ThreadSafeBus(
            channel=self.CHANNEL_1,
            bustype=self.INTERFACE_1,
            bitrate=self.BITRATE,
            fd=TEST_CAN_FD,
            single_handle=True,
        )
        self.bus2 = can.ThreadSafeBus(
            channel=self.CHANNEL_2,
            bustype=self.INTERFACE_2,
            bitrate=self.BITRATE,
            fd=TEST_CAN_FD,
            single_handle=True,
        )

    @pytest.mark.timeout(5.0)
    def test_concurrent_writes(self):
        sender_pool = ThreadPool(100)
        receiver_pool = ThreadPool(100)

        message = can.Message(
            arbitration_id=0x123,
            channel=self.CHANNEL_1,
            is_extended_id=True,
            timestamp=121334.365,
            data=[254, 255, 1, 2],
        )
        workload = 1000 * [message]

        def sender(msg):
            self.bus1.send(msg)

        def receiver(_):
            return self.bus2.recv(timeout=2.0)

        sender_pool.map_async(sender, workload)
        for msg in receiver_pool.map(receiver, len(workload) * [None]):
            self.assertIsNotNone(msg)
            self.assertEqual(message.arbitration_id, msg.arbitration_id)
            self.assertTrue(message.equals(msg, timestamp_delta=None))

        sender_pool.close()
        sender_pool.join()
        receiver_pool.close()
        receiver_pool.join()

    @pytest.mark.timeout(5.0)
    def test_filtered_bus(self):
        sender_pool = ThreadPool(100)
        receiver_pool = ThreadPool(100)

        included_message = can.Message(
            arbitration_id=0x123,
            channel=self.CHANNEL_1,
            is_extended_id=True,
            timestamp=121334.365,
            data=[254, 255, 1, 2],
        )
        excluded_message = can.Message(
            arbitration_id=0x02,
            channel=self.CHANNEL_1,
            is_extended_id=True,
            timestamp=121334.300,
            data=[1, 2, 3],
        )
        workload = 500 * [included_message] + 500 * [excluded_message]
        random.shuffle(workload)

        self.bus2.set_filters([{"can_id": 0x123, "can_mask": 0xFF, "extended": True}])

        def sender(msg):
            self.bus1.send(msg)

        def receiver(_):
            return self.bus2.recv(timeout=2.0)

        sender_pool.map_async(sender, workload)
        received_msgs = receiver_pool.map(receiver, 500 * [None])

        for msg in received_msgs:
            self.assertIsNotNone(msg)
            self.assertEqual(msg.arbitration_id, included_message.arbitration_id)
            self.assertTrue(included_message.equals(msg, timestamp_delta=None))
        self.assertEqual(len(received_msgs), 500)

        sender_pool.close()
        sender_pool.join()
        receiver_pool.close()
        receiver_pool.join()


if __name__ == "__main__":
    unittest.main()

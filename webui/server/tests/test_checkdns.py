"""Client check: DNS wire format, zone handling, nonce bookkeeping, warnings."""

import socket
import struct
import unittest

from lanjaeger_webui import checkdns, envfile


def query(name: str, qtype: int = 1, qid: int = 0x1234) -> bytes:
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    return struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + q + struct.pack(">HH", qtype, 1)


def parse_answer(resp: bytes):
    qid, flags, qd, an = struct.unpack(">HHHH", resp[:8])
    ip = None
    if an:
        ip = socket.inet_ntoa(resp[-4:])
    return qid, flags & 0xF, an, ip


class Wire(unittest.TestCase):
    def setUp(self):
        self.store = checkdns.Store()

    def test_nonce_a_record(self):
        check = self.store.new_check("192.168.1.21")
        n = check["nonces"][0]
        resp = checkdns.build_response(query(f"{n}.{checkdns.ZONE}"), "192.168.1.10", self.store)
        self.assertEqual(parse_answer(resp), (0x1234, 0, 1, "192.168.1.10"))
        self.assertEqual(self.store.result(check["id"])["seen"], 1)

    def test_aaaa_is_empty_noerror(self):
        check = self.store.new_check("x")
        resp = checkdns.build_response(query(f"{check['nonces'][1]}.{checkdns.ZONE}", qtype=28), "192.168.1.10", self.store)
        self.assertEqual(parse_answer(resp)[1:3], (0, 0))      # NOERROR, no answer
        self.assertEqual(self.store.result(check["id"])["seen"], 1)  # still counts as reaching us

    def test_outside_zone_nxdomain_and_not_recorded(self):
        resp = checkdns.build_response(query("www.example.com"), "192.168.1.10", self.store)
        self.assertEqual(parse_answer(resp)[1], 3)
        self.assertEqual(self.store.seen, {})

    def test_garbage_ignored(self):
        for data in (b"", b"\x00" * 5, query("a.b")[:15], struct.pack(">HHHHHH", 1, 0x8000, 1, 0, 0, 0)):
            self.assertIsNone(checkdns.build_response(data, "1.2.3.4", self.store))

    def test_result_counts_only_own_nonces(self):
        a, b = self.store.new_check("a"), self.store.new_check("b")
        for n in a["nonces"][:4]:
            self.store.mark(n)
        self.assertEqual((self.store.result(a["id"])["seen"], self.store.result(b["id"])["seen"]), (4, 0))
        self.assertIsNone(self.store.result("0" * 16))

    def test_responder_over_udp(self):
        r = checkdns.Responder(self.store, lambda: "10.0.0.5", port=0)
        port = r.bind()
        r.start()
        check = self.store.new_check("c")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.sendto(query(f"{check['nonces'][0]}.{checkdns.ZONE}"), ("127.0.0.1", port))
        self.assertEqual(parse_answer(s.recv(512))[3], "10.0.0.5")


class Warnings(unittest.TestCase):
    def test_public_lancache_ip_and_dns_loop(self):
        w = envfile.warnings({"LANCACHE_IP": "192.168.1.10 85.1.2.3", "DNS_BIND_IP": "192.168.1.10",
                              "UPSTREAM_DNS": "192.168.1.10; 1.1.1.1"})
        self.assertEqual(len(w), 2)
        self.assertIn("85.1.2.3", w[0])
        self.assertIn("loop", w[1])
        self.assertEqual(envfile.warnings({"LANCACHE_IP": "10.0.0.2", "DNS_BIND_IP": "10.0.0.2",
                                           "UPSTREAM_DNS": "1.1.1.1"}), [])

    def test_ipv6_field(self):
        clean, errors = envfile.validate({"DNS_BIND_IP6": "FD00::10"})
        self.assertEqual(clean["DNS_BIND_IP6"], "fd00::10")
        self.assertIn("DNS_BIND_IP6", envfile.validate({"DNS_BIND_IP6": "192.168.1.1"})[1])


if __name__ == "__main__":
    unittest.main()

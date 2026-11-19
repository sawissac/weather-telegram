"""Offline tests: feeds are faked, nothing hits the network or Telegram.

Run:  python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import disaster_alert_bot as bot  # noqa: E402

FUTURE = (datetime.now(timezone(timedelta(hours=7))) + timedelta(hours=12)).isoformat(timespec="seconds")
PAST = (datetime.now(timezone(timedelta(hours=7))) - timedelta(hours=12)).isoformat(timespec="seconds")

TMD_RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0"><channel>
<title>Latest alerts from TMD</title>
<item><title>Very Heavy Rain</title><link>https://www.tmd.go.th/uploads/CAP/en/CAP1.xml</link>
<guid isPermaLink="false">TMD1-en</guid><pubDate>Thu, 19 Nov 2026 10:15:00 +0700</pubDate></item>
<item><title>Heavy Rain</title><link>https://www.tmd.go.th/uploads/CAP/en/CAP2.xml</link>
<guid isPermaLink="false">TMD2-en</guid></item>
<item><title>Old Rain</title><link>https://www.tmd.go.th/uploads/CAP/en/CAP3.xml</link>
<guid isPermaLink="false">TMD3-en</guid></item>
</channel></rss>"""


def cap(ident, event, severity, area, expires, msg_type="Alert", status="Actual"):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
<identifier>{ident}</identifier><sender>TMD</sender><sent>2026-11-19T10:00:00+07:00</sent>
<status>{status}</status><msgType>{msg_type}</msgType><scope>Public</scope>
<info><language>en-US</language><category>Met</category><event>{event}</event>
<urgency>Immediate</urgency><severity>{severity}</severity><certainty>Likely</certainty>
<effective>2026-11-19T10:00:00+07:00</effective><expires>{expires}</expires>
<senderName>TMD</senderName><headline>{event} Risk Area</headline>
<description>Heavy rain expected. Beware of flash floods &amp; landslides.</description>
<instruction>Keep away from rivers and foothills.</instruction>
<web>https://www.tmd.go.th/en/forecast/daily</web>
<area><areaDesc>{area}</areaDesc></area></info></alert>""".encode()


STORM_RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Storm Tracking</title>
<item><title>Heavy to very heavy rains in the South No.9 (317/2026)</title>
<author>Thai Meteorological Department</author>
<description><![CDATA[Heavy rain in Songkhla, Yala and Narathiwat.<br/> Watch for flash floods.]]></description></item>
</channel></rss>"""

BMKG_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>BMKG</title>
<item><title>Heavy rain in DKI Jakarta</title><link>https://cuaca.bmkg.go.id/page.html</link>
<guid>BMKG-A1</guid><description>Moderate to heavy rain accompanied by lightning in Jakarta Selatan.</description></item>
</channel></rss>"""

INATEWS_ATOM = f"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>InaTEWS</title>
<entry><id>EQ-TSU-1</id><title>Tsunami warning</title>
<content type="text/xml"><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
<identifier>EQ-TSU-1</identifier><status>Actual</status><msgType>Alert</msgType>
<info><language>en</language><event>Tsunami Warning</event><severity>Extreme</severity>
<urgency>Immediate</urgency><certainty>Observed</certainty><expires>{FUTURE}</expires>
<headline>M7.6 earthquake, potential tsunami</headline><area><areaDesc>North Sulawesi</areaDesc></area></info>
</alert></content></entry></feed>""".encode()

AUTOGEMPA = json.dumps({"Infogempa": {"gempa": {
    "Tanggal": "19 Nov 2026", "Jam": "10:12:45 WIB", "DateTime": "2026-11-19T03:12:45+00:00",
    "Coordinates": "-8.41,121.39", "Magnitude": "6.1", "Kedalaman": "15 km",
    "Wilayah": "30 km TimurLaut MBAY-NAGEKEO-NTT", "Potensi": "Tidak berpotensi tsunami",
    "Dirasakan": "IV Mbay", "Shakemap": "20261119101245.mmi.jpg"}}}).encode()
GEMPATERKINI = json.dumps({"Infogempa": {"gempa": [
    {"Tanggal": "19 Nov 2026", "Jam": "10:12:45 WIB", "DateTime": "2026-11-19T03:12:45+00:00",
     "Coordinates": "-8.41,121.39", "Magnitude": "6.1", "Kedalaman": "15 km",
     "Wilayah": "30 km TimurLaut MBAY-NAGEKEO-NTT", "Potensi": "Tidak berpotensi tsunami"},
    {"Tanggal": "18 Nov 2026", "Jam": "05:48:16 WIB", "DateTime": "2026-11-17T22:48:16+00:00",
     "Coordinates": "1.25,126.27", "Magnitude": "5.2", "Kedalaman": "62 km",
     "Wilayah": "129 km Tenggara BITUNG-SULUT", "Potensi": "Berpotensi Tsunami"},
    {"Tanggal": "17 Nov 2026", "Jam": "01:00:00 WIB", "DateTime": "2026-11-16T18:00:00+00:00",
     "Coordinates": "-6.0,105.0", "Magnitude": "4.1", "Kedalaman": "10 km",
     "Wilayah": "Selat Sunda", "Potensi": "Tidak berpotensi tsunami"},
]}}).encode()
USGS = json.dumps({"features": [{"id": "us7000abcd", "properties": {
    "mag": 5.8, "place": "40 km NNE of Tachileik, Myanmar", "time": 1795000000000,
    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abcd", "tsunami": 0},
    "geometry": {"coordinates": [100.1, 20.8, 10.0]}}]}).encode()


def fake_fetch(responses):
    def _get(url, cfg, params=None, retries=2, timeout=25):
        for prefix, body in responses.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body
        raise RuntimeError(f"unexpected URL {url}")
    return _get


BASE_RESPONSES = {
    bot.FEEDS["tmd_cap"]["en"]: TMD_RSS,
    "https://www.tmd.go.th/uploads/CAP/en/CAP1.xml": cap("TMD1", "Very Heavy Rain", "Extreme", "Chiang Mai, Lamphun", FUTURE),
    "https://www.tmd.go.th/uploads/CAP/en/CAP2.xml": cap("TMD2", "Heavy Rain", "Moderate", "Bangkok", FUTURE),
    "https://www.tmd.go.th/uploads/CAP/en/CAP3.xml": cap("TMD3", "Old Rain", "Extreme", "Phuket", PAST),
    bot.FEEDS["tmd_storm"]["en"]: STORM_RSS,
    bot.FEEDS["bmkg_cap"]["en"]: BMKG_RSS,
    "https://cuaca.bmkg.go.id/page.html": b"<html><body>not xml</body></html>",
    bot.FEEDS["bmkg_inatews"]: INATEWS_ATOM,
    bot.FEEDS["bmkg_quake_latest"]: AUTOGEMPA,
    bot.FEEDS["bmkg_quake_m5"]: GEMPATERKINI,
    bot.FEEDS["usgs"]: USGS,
}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for k in list(os.environ):
            if k in ("AREA_KEYWORDS", "MIN_SEVERITY", "HOME_LAT", "HOME_LON", "QUAKE_RADIUS_KM", "COUNTRIES"):
                del os.environ[k]
        os.environ["STATE_FILE"] = os.path.join(self.tmp, "state.json")
        os.environ["TELEGRAM_BOT_TOKEN"] = "x"
        os.environ["TELEGRAM_CHAT_ID"] = "1"
        bot.http_get = fake_fetch(dict(BASE_RESPONSES))

    def run_once(self):
        cfg = bot.Config()
        n = bot.Notifier(cfg, dry_run=True)
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            bot.check_once(cfg, n)
        return n.sent

    def test_first_run_sends_only_summary(self):
        sent = self.run_once()
        self.assertEqual(len(sent), 1)
        self.assertIn("bot is running", sent[0])
        self.assertIn("Very Heavy Rain", sent[0])     # active, Extreme
        self.assertIn("Tsunami Warning", sent[0])     # InaTEWS, Extreme
        self.assertNotIn("Old Rain", sent[0])         # expired
        self.assertNotIn("🟡", sent[0])               # Moderate (Bangkok) < Severe
        self.assertIn("DKI Jakarta", sent[0])         # BMKG item without CAP -> bulletin

    def test_second_run_no_duplicates_then_new_alert(self):
        self.run_once()
        self.assertEqual(self.run_once(), [])
        # a new TMD alert appears
        new_rss = TMD_RSS.replace(b"<channel>", b"""<channel><item><title>Strong Wind</title>
<link>https://www.tmd.go.th/uploads/CAP/en/CAP9.xml</link><guid>TMD9-en</guid></item>""")
        resp = dict(BASE_RESPONSES)
        resp[bot.FEEDS["tmd_cap"]["en"]] = new_rss
        resp["https://www.tmd.go.th/uploads/CAP/en/CAP9.xml"] = cap("TMD9", "Strong Wind", "Severe", "Chiang Rai", FUTURE)
        bot.http_get = fake_fetch(resp)
        sent = self.run_once()
        self.assertEqual(len(sent), 1)
        self.assertIn("SEVERE — Strong Wind", sent[0])
        self.assertIn("Chiang Rai", sent[0])
        self.assertIn("What to do", sent[0])

    def test_area_filter(self):
        os.environ["AREA_KEYWORDS"] = "chiang mai"
        cfg = bot.Config()
        alerts, _ = bot.collect_cap_feed("tmd_cap", bot.FEEDS["tmd_cap"]["en"], cfg, {})
        picked = [a["title"] for a in alerts if bot.should_notify(a, cfg)]
        self.assertEqual(picked, ["Very Heavy Rain"])

    def test_quakes_and_tsunami(self):
        cfg = bot.Config()
        alerts, ids = bot.collect_bmkg_quakes(cfg, {})
        self.assertEqual(len(alerts), 3)  # duplicate between feeds removed
        picked = {a["place"]: a for a in alerts if bot.should_notify(a, cfg)}
        self.assertIn("30 km TimurLaut MBAY-NAGEKEO-NTT", picked)   # M6.1
        self.assertTrue(picked["129 km Tenggara BITUNG-SULUT"]["tsunami"])
        self.assertNotIn("Selat Sunda", picked)                     # M4.1
        msg = bot.format_alert(picked["129 km Tenggara BITUNG-SULUT"], cfg)
        self.assertIn("🌊", msg)

    def test_quake_radius(self):
        os.environ.update({"HOME_LAT": "18.79", "HOME_LON": "98.98", "QUAKE_RADIUS_KM": "500"})  # Chiang Mai
        cfg = bot.Config()
        alerts, _ = bot.collect_usgs(cfg, {})
        self.assertTrue(bot.should_notify(alerts[0], cfg))  # Tachileik ~ 260 km
        bm, _ = bot.collect_bmkg_quakes(cfg, {})
        picked = [a for a in bm if bot.should_notify(a, cfg)]
        self.assertEqual([a["tsunami"] for a in picked], [True])  # only tsunami one passes radius
        self.assertIn("km from your location", bot.format_alert(alerts[0], cfg))

    def test_failed_source_does_not_break_others_and_primes_later(self):
        resp = dict(BASE_RESPONSES)
        resp[bot.FEEDS["bmkg_cap"]["en"]] = RuntimeError("down")
        bot.http_get = fake_fetch(resp)
        self.run_once()  # baseline, BMKG CAP down
        bot.http_get = fake_fetch(dict(BASE_RESPONSES))
        self.assertEqual(self.run_once(), [])  # BMKG CAP backlog recorded quietly

    def test_fresh_start_keeps_subscribed_groups(self):
        self.run_once()
        state = bot.load_state(os.environ["STATE_FILE"])
        state["subscribers"] = {"-100": "Family"}
        bot.save_state(os.environ["STATE_FILE"], state)
        os.remove(os.environ["STATE_FILE"])  # user deletes state.json to start fresh
        cfg = bot.Config()
        n = bot.Notifier(cfg, dry_run=True)
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            bot.check_once(cfg, n)
        self.assertEqual(n.targets(), ["1", "-100"])  # summary goes to the group too
        self.assertIn("bot is running", n.sent[0])
        self.assertEqual(bot.load_state(os.environ["STATE_FILE"])["subscribers"], {"-100": "Family"})

    def test_google_maps_links(self):
        cfg = bot.Config()
        el = bot._strip_ns(bot.ET.fromstring(cap("M1", "Heavy Rain", "Severe", "Songkhla", FUTURE).replace(
            b"<areaDesc>Songkhla</areaDesc>",
            b"<areaDesc>Songkhla</areaDesc><polygon>7.0,100.0 7.4,100.0 7.4,100.6 7.0,100.0</polygon>")))
        spot = bot.parse_cap(el, "en")["map"]
        self.assertAlmostEqual(spot["lat"], 7.2)
        self.assertAlmostEqual(spot["lon"], 100.3)
        msg = bot.format_alert({"kind": "warning", "source": "tmd_cap", "title": "Heavy Rain", "severity": 3,
                                "severity_label": "Severe", "map": spot, "link": "https://x"}, cfg)
        self.assertIn("map_action=map&amp;center=7.2000,100.3000&amp;zoom=", msg)
        self.assertIn("Google Maps</a> · <a", msg)
        # quake: pin on the epicentre
        quakes, _ = bot.collect_bmkg_quakes(cfg, {})
        self.assertIn("google.com/maps/search/?api=1&amp;query=", bot.format_alert(quakes[0], cfg))
        # bulletin without coordinates: no map link
        self.assertNotIn("Google Maps", bot.format_alert({"kind": "bulletin", "source": "tmd_storm",
                                                          "title": "Rain", "severity": 0,
                                                          "severity_label": "Bulletin"}, cfg))

    def test_html_escaping(self):
        cfg = bot.Config()
        a = {"kind": "bulletin", "source": "tmd_storm", "title": "Rain <b>& wind", "severity": 0,
             "severity_label": "Bulletin", "description": "x < y", "link": "https://a.b/?q=1&r=2"}
        msg = bot.format_alert(a, cfg)
        self.assertIn("&lt;b&gt;&amp; wind", msg)
        self.assertIn("x &lt; y", msg)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for k in ("AREA_KEYWORDS", "COUNTRIES"):
            os.environ.pop(k, None)
        os.environ["STATE_FILE"] = os.path.join(self.tmp, "state.json")
        os.environ["TELEGRAM_BOT_TOKEN"] = "x"
        os.environ["TELEGRAM_CHAT_ID"] = "1"
        self._real_api = bot.telegram_api

    def tearDown(self):
        bot.telegram_api = self._real_api

    def test_province_counts(self):
        self.assertEqual(sum(len(p) for _, p in bot.PROVINCES["TH"]["regions"]), 77)
        self.assertEqual(sum(len(p) for _, p in bot.PROVINCES["ID"]["regions"]), 38)

    def test_command_variants(self):
        cfg = bot.Config()
        for text in ("/country-list-provience", "/country_list_province", "/country_list_province@MyBot"):
            replies = bot.handle_command(text, cfg)
            self.assertEqual(len(replies), 3, text)  # TH, ID, settings footer
        th_only = bot.handle_command("/country_list_province th", cfg)
        self.assertEqual(len(th_only), 2)
        self.assertIn("77 provinces", th_only[0])
        self.assertIn("Unknown country", bot.handle_command("/provinces mars", cfg)[0])
        self.assertIsNone(bot.handle_command("/other_bot_cmd", cfg))
        self.assertIsNone(bot.handle_command("hello", cfg))

    def test_province_marks_area_keywords(self):
        os.environ["AREA_KEYWORDS"] = "Songkhla,Jawa Barat"
        replies = bot.handle_command("/country_list_province", bot.Config())
        self.assertIn("✅ Songkhla", replies[0])
        self.assertIn("✅ Jawa Barat (West Java)", replies[1])
        self.assertNotIn("✅ Jawa Timur", replies[1])

    def test_warnings_command_lists_all_and_splits(self):
        active = {f"bmkg_cap:{i}": {"title": f"Thunderstorm {i} " + "x" * 80, "icon": "🟡", "expires": FUTURE}
                  for i in range(60)}
        state = {"active": active}
        welcome = bot.group_welcome(bot.Config(), {"active": dict(active)})
        self.assertIn("…and 45 more. Send /warnings", welcome)
        replies = bot.handle_command("/warnings", bot.Config(), state)
        self.assertGreater(len(replies), 1)                 # too long for one message
        self.assertTrue(all(len(r) <= 4096 for r in replies))
        joined = "\n".join(replies)
        for i in range(60):
            self.assertIn(f"Thunderstorm {i} ", joined)
        self.assertNotIn("more.", joined)

    def test_active_list_shows_country_flag(self):
        state = {"active": {"tmd_cap:1": {"title": "Heavy Rain", "icon": "🟠", "expires": FUTURE},
                            "bmkg_cap:2": {"title": "Thunderstorm · Aceh", "icon": "🟠", "expires": FUTURE}}}
        text = bot.handle_command("/warnings", bot.Config(), state)[0]
        self.assertIn("🇹🇭🟠 Heavy Rain", text)
        self.assertIn("🇮🇩🟠 Thunderstorm · Aceh", text)

    def test_area_match_ignores_spacing_and_spelling(self):
        alert = {"area": "Nakhon Sri Thammarat Pattalung Kep. Riau"}
        self.assertTrue(bot.matches_area(alert, ["nakhon si thammarat"]))
        self.assertTrue(bot.matches_area(alert, ["phatthalung"]))
        self.assertTrue(bot.matches_area(alert, ["kepulauan riau"]))
        self.assertFalse(bot.matches_area(alert, ["songkhla"]))

    def _run_updates(self, updates, state=None):
        bot.save_state(os.environ["STATE_FILE"], state or {"seen": {}})
        calls = []

        def fake_api(cfg, method, payload=None, timeout=30):
            calls.append((method, payload))
            if method == "getUpdates":
                return {"ok": True, "result": updates}
            return {"ok": True}

        bot.telegram_api = fake_api
        cfg = bot.Config()
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            bot.handle_telegram_updates(cfg, bot.Notifier(cfg))
        sends = [p for m, p in calls if m == "sendMessage"]
        return sends, bot.load_state(os.environ["STATE_FILE"])

    def test_bot_added_to_group_subscribes_and_welcomes(self):
        active = {"tmd_cap:1": {"title": "Heavy Rain", "icon": "🟠", "expires": FUTURE},
                  "tmd_cap:2": {"title": "Old Rain", "icon": "🔴", "expires": PAST}}
        group = {"id": -100, "type": "supergroup", "title": "Family"}
        sends, state = self._run_updates([{"update_id": 1, "my_chat_member": {
            "chat": group, "new_chat_member": {"status": "member"}}}], {"seen": {}, "active": active})
        self.assertEqual(state["subscribers"], {"-100": "Family"})
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]["chat_id"], "-100")
        self.assertIn("Disaster alerts are on", sends[0]["text"])
        self.assertIn("Heavy Rain", sends[0]["text"])
        self.assertNotIn("Old Rain", sends[0]["text"])  # expired

    def test_missed_add_event_group_subscribes_on_first_command(self):
        group = {"id": -300, "type": "group", "title": "P&M Weather"}
        sends, state = self._run_updates([
            {"update_id": 20, "message": {"chat": group, "text": "/start"}},
            {"update_id": 21, "message": {"chat": group, "text": "/country_list_province@P_MWeatherBot TH"}},
        ])
        self.assertIn("-300", state["subscribers"])
        texts = [p["text"] for p in sends]
        self.assertIn("Disaster alerts are on", texts[0])   # /start answered by the welcome
        self.assertIn("77 provinces", texts[1])
        self.assertEqual(len(sends), 3)                     # welcome + TH list + footer

    def test_new_member_gets_welcome_without_typing(self):
        state = {"seen": {}, "subscribers": {"-100": "Family"}}
        sends, state = self._run_updates([{"update_id": 5, "message": {
            "chat": {"id": -100, "type": "supergroup"},
            "new_chat_members": [{"first_name": "Mali"}, {"first_name": "SomeBot", "is_bot": True}]}}], state)
        self.assertEqual(len(sends), 1)
        self.assertIn("Welcome Mali", sends[0]["text"])
        self.assertNotIn("SomeBot", sends[0]["text"])
        # a second join right away is not greeted again (cooldown)
        sends, _ = self._run_updates([{"update_id": 6, "message": {
            "chat": {"id": -100, "type": "supergroup"}, "new_chat_members": [{"first_name": "Ann"}]}}], state)
        self.assertEqual(sends, [])

    def test_bot_removed_unsubscribes_and_alerts_go_to_groups(self):
        state = {"seen": {}, "subscribers": {"-100": "Family", "-200": "Work"}}
        _, state = self._run_updates([{"update_id": 9, "my_chat_member": {
            "chat": {"id": -200, "type": "group"}, "new_chat_member": {"status": "kicked"}}}], state)
        self.assertEqual(list(state["subscribers"]), ["-100"])
        n = bot.Notifier(bot.Config())
        n.subscribers = set(state["subscribers"])
        self.assertEqual(n.targets(), ["1", "-100"])  # TELEGRAM_CHAT_ID + subscribed group

    def _run_updates_admins(self, updates, state, admins=(7,)):
        """Like _run_updates, but getChatMember says user ids in `admins` are group admins."""
        bot.save_state(os.environ["STATE_FILE"], state)
        calls = []

        def fake_api(cfg, method, payload=None, timeout=30):
            calls.append((method, payload))
            if method == "getUpdates":
                return {"ok": True, "result": updates}
            if method == "getChatMember":
                status = "administrator" if payload["user_id"] in admins else "member"
                return {"ok": True, "result": {"status": status}}
            return {"ok": True}

        bot.telegram_api = fake_api
        cfg = bot.Config()
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            bot.handle_telegram_updates(cfg, bot.Notifier(cfg))
        return [p for m, p in calls if m == "sendMessage"], bot.load_state(os.environ["STATE_FILE"])

    def test_stop_mutes_group_until_resume(self):
        group = {"id": -100, "type": "supergroup", "title": "ID group"}
        state = {"seen": {}, "subscribers": {"-100": "ID group", "-200": "TH group"}}
        # a normal member can't stop it
        sends, state = self._run_updates_admins([
            {"update_id": 1, "message": {"chat": group, "from": {"id": 8}, "text": "/stop"}}], state)
        self.assertIn("Only group admins", sends[0]["text"])
        self.assertIn("-100", state["subscribers"])
        # an admin can
        sends, state = self._run_updates_admins([
            {"update_id": 2, "message": {"chat": group, "from": {"id": 7}, "text": "/stop@P_MWeatherBot"}}], state)
        self.assertIn("stop here", sends[0]["text"])
        self.assertNotIn("-100", state["subscribers"])
        self.assertEqual(state["muted"], {"-100": "ID group"})
        # silent afterwards: commands, province names, re-subscribe on first message
        sends, state = self._run_updates_admins([
            {"update_id": 3, "message": {"chat": group, "from": {"id": 8}, "text": "/country_list_province"}},
            {"update_id": 4, "message": {"chat": group, "from": {"id": 8}, "text": "Bali"}},
            {"update_id": 5, "message": {"chat": group, "from": {"id": 8}, "text": "/resume"}},  # not admin
        ], state)
        self.assertEqual(sends, [])
        self.assertNotIn("-100", state["subscribers"])
        # alerts skip it, even if it were still listed somewhere
        n = bot.Notifier(bot.Config())
        n.subscribers, n.muted = {"-100", "-200"}, set(state["muted"])
        self.assertEqual(n.targets(), ["1", "-200"])
        # survives a fresh state.json
        self.assertEqual(bot.load_muted(os.environ["STATE_FILE"]), {"-100": "ID group"})
        # admin resumes
        sends, state = self._run_updates_admins([
            {"update_id": 6, "message": {"chat": group, "from": {"id": 7}, "text": "/resume"}}], state)
        self.assertIn("back on", sends[0]["text"])
        self.assertIn("-100", state["subscribers"])
        self.assertEqual(state["muted"], {})

    def test_owner_lists_and_stops_chats_by_id(self):
        owner = {"id": 1, "type": "private"}
        state = {"seen": {}, "subscribers": {"-100": "ID group", "-200": "TH group"}}
        sends, state = self._run_updates_admins([
            {"update_id": 1, "message": {"chat": owner, "text": "/chats"}},
            {"update_id": 2, "message": {"chat": owner, "text": "/stop -100"}},
            {"update_id": 3, "message": {"chat": owner, "text": "/stop -999"}},
        ], state)
        self.assertIn("-100", sends[0]["text"])
        self.assertIn("TH group", sends[0]["text"])
        self.assertIn("Stopped: ID group", sends[1]["text"])
        self.assertIn("Unknown chat", sends[2]["text"])
        self.assertEqual(list(state["subscribers"]), ["-200"])
        self.assertIn("-100", state["muted"])
        sends, state = self._run_updates_admins([
            {"update_id": 4, "message": {"chat": owner, "text": "/resume -100"}}], state)
        self.assertIn("back on for ID group", sends[0]["text"])
        self.assertIn("-100", state["subscribers"])

    def test_poll_replies_only_to_allowed_chat_and_saves_offset(self):
        bot.save_state(os.environ["STATE_FILE"], {"seen": {}})
        calls = []

        def fake_api(cfg, method, payload=None, timeout=30):
            calls.append((method, payload))
            if method == "getUpdates":
                return {"ok": True, "result": [
                    {"update_id": 10, "message": {"chat": {"id": 1}, "text": "/country-list-provience ID"}},
                    {"update_id": 11, "message": {"chat": {"id": 999}, "text": "/country_list_province"}},
                    {"update_id": 12, "message": {"chat": {"id": 1}, "text": "just chatting"}},
                ]}
            return {"ok": True}

        bot.telegram_api = fake_api
        cfg = bot.Config()
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            bot.handle_telegram_updates(cfg, bot.Notifier(cfg))
        sends = [p for m, p in calls if m == "sendMessage"]
        self.assertEqual({p["chat_id"] for p in sends}, {"1"})
        self.assertEqual(len(sends), 2)  # Indonesia list + footer
        self.assertIn("38 provinces", sends[0]["text"])
        self.assertEqual(bot.load_state(os.environ["STATE_FILE"])["tg_offset"], 13)

    def test_command_answered_once_when_a_send_fails(self):
        # A network error while replying must not leave the offset unsaved:
        # that made the bot re-read the same command and answer it again and again.
        import urllib.error
        bot.save_state(os.environ["STATE_FILE"], {"seen": {}, "subscribers": {"-100": "TH group"}})
        handled = []

        def fake_api(cfg, method, payload=None, timeout=30):
            if method == "getUpdates":
                if int(payload["offset"]) > 5:
                    return {"ok": True, "result": []}
                handled.append(payload["offset"])
                return {"ok": True, "result": [{"update_id": 5, "message": {
                    "chat": {"id": -100, "type": "supergroup"}, "text": "/country_list_province"}}]}
            raise urllib.error.URLError("timed out")

        bot.telegram_api = fake_api
        real_sleep, bot.time.sleep = bot.time.sleep, lambda s: None
        cfg = bot.Config()
        import io, contextlib
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                for _ in range(3):
                    bot.handle_telegram_updates(cfg, bot.Notifier(cfg))
        finally:
            bot.time.sleep = real_sleep
        self.assertEqual(len(handled), 1)
        self.assertEqual(bot.load_state(os.environ["STATE_FILE"])["tg_offset"], 6)

    def test_offset_from_old_bot_token_does_not_replay_updates(self):
        # state.json kept the offset of a previous bot token (higher than the new bot's
        # update ids): the same updates were returned and answered every poll.
        bot.save_state(os.environ["STATE_FILE"], {"seen": {}, "subscribers": {"-100": "TH"},
                                                  "tg_offset": 356407947, "tg_bot_id": "old"})
        handled = []

        def fake_api(cfg, method, payload=None, timeout=30):
            if method == "getUpdates":
                offset = int(payload["offset"])
                result = [{"update_id": 105210385, "message": {"chat": {"id": -100, "type": "supergroup"},
                                                                "text": "/country_list_province TH"}}]
                result = [u for u in result if u["update_id"] >= offset]
                handled.extend(u["update_id"] for u in result)
                return {"ok": True, "result": result}
            return {"ok": True}

        bot.telegram_api = fake_api
        cfg = bot.Config()
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(3):
                bot.handle_telegram_updates(cfg, bot.Notifier(cfg))
        self.assertEqual(handled, [105210385])
        self.assertEqual(bot.load_state(os.environ["STATE_FILE"])["tg_offset"], 105210386)

    def test_find_province(self):
        self.assertEqual(bot.find_province("chiang mai")[1], "Chiang Mai")
        self.assertEqual(bot.find_province("West Java")[1], "Jawa Barat")
        self.assertEqual(bot.find_province("Bandung")[1], "Jawa Barat")
        self.assertEqual(bot.find_province("Nakhon Sri Thammarat")[1], "Nakhon Si Thammarat")
        self.assertEqual(bot.find_province("Papua")[1], "Papua")  # exact beats the 5 other Papua provinces
        self.assertIsInstance(bot.find_province("Jawa"), list)  # ambiguous
        self.assertIsNone(bot.find_province("hello everyone", exact_only=True))
        self.assertIsNone(bot.find_province("Jawa", exact_only=True))
        for label_list in (r for c in bot.PROVINCES.values() for _, r in c["regions"]):
            for label in label_list:
                self.assertIn(label.partition(" (")[0], bot.PROVINCE_COORDS)

    def _weather_api(self, calls):
        def fake_api(cfg, method, payload=None, timeout=30):
            calls.append((method, payload))
            if method == "getUpdates":
                return {"ok": True, "result": [
                    {"update_id": 20, "message": {"chat": {"id": -100, "type": "supergroup"}, "text": "Chiang Mai"}},
                    {"update_id": 21, "message": {"chat": {"id": -100, "type": "supergroup"}, "text": "good morning all"}},
                    {"update_id": 22, "message": {"chat": {"id": -100, "type": "supergroup"}, "text": "/weather songkhla"}},
                ]}
            return {"ok": True}
        return fake_api

    def test_province_name_message_gets_weather(self):
        state = {"seen": {}, "subscribers": {"-100": "TH group"}, "active": {
            "a": {"title": "Heavy Rain · Songkhla Pattani", "icon": "🟠", "expires": FUTURE},
            "b": {"title": "Heavy to very heavy rains in the South No.9", "icon": "📢", "expires": FUTURE},
            "c": {"title": "Thunderstorm · Jawa Barat", "icon": "🟡", "expires": FUTURE}}}
        bot.save_state(os.environ["STATE_FILE"], state)
        weather = json.dumps({
            "current": {"time": "2026-11-19T15:30", "temperature_2m": 27.2, "apparent_temperature": 32.1,
                        "relative_humidity_2m": 80, "wind_speed_10m": 4.7, "precipitation": 0, "weather_code": 81},
            "daily": {"time": ["2026-11-19", "2026-11-20", "2026-11-21"], "weather_code": [95, 3, 0],
                      "temperature_2m_min": [23, 22, 22], "temperature_2m_max": [30, 29, 29],
                      "precipitation_sum": [12.5, 0, 0], "precipitation_probability_max": [90, 20, 5]}}).encode()
        calls = []
        bot.telegram_api = self._weather_api(calls)
        real_get, bot.http_get = bot.http_get, lambda url, cfg, params=None, **kw: weather
        cfg = bot.Config()
        import io, contextlib
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                bot.handle_telegram_updates(cfg, bot.Notifier(cfg))
        finally:
            bot.http_get = real_get
        sends = [p["text"] for m, p in calls if m == "sendMessage"]
        self.assertEqual(len(sends), 2)  # "Chiang Mai" + /weather songkhla; normal chat ignored
        self.assertIn("Chiang Mai", sends[0])
        self.assertIn("27°C", sends[0])
        self.assertIn("No official warnings", sends[0])
        self.assertIn("Songkhla", sends[1])
        self.assertIn("Heavy Rain", sends[1])
        self.assertIn("rains in the South", sends[1])  # TMD region bulletin counts for southern provinces
        self.assertNotIn("Jawa Barat", sends[1])


if __name__ == "__main__":
    unittest.main()

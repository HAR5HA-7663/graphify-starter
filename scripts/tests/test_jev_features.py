"""Offline tests for the Jev-backed features. No network: jev.ask is stubbed.

    cd ~/brain && .venv/bin/python -m unittest scripts.tests.test_jev_features -v
"""

import unittest
from datetime import date
from unittest import mock

from scripts import config, jev, pages, rerank, staleness


def cand(score, layer="brain_wiki", src="wiki/pages/a.md", text="t"):
    return {"score": score, "layer": layer, "source_rel": src, "heading_path": "H", "text": text}


def nouls(*ps):
    return {f"p{i}": {"type": "noul", "noul": p} for i, p in enumerate(ps, start=1)}


class RerankBand(unittest.TestCase):
    def test_only_ambiguous_scores_consult_jev(self):
        lo, hi = config.RERANK_BAND
        self.assertFalse(rerank.in_band(lo - 0.01))   # clear miss: no added latency
        self.assertTrue(rerank.in_band(lo))
        self.assertTrue(rerank.in_band(config.TRAINING_FALLBACK_FLOOR))
        self.assertFalse(rerank.in_band(hi))          # clear hit: no added latency

    def test_band_covers_both_cosine_thresholds(self):
        lo, hi = config.RERANK_BAND
        self.assertLess(lo, config.TRAINING_FALLBACK_FLOOR)
        self.assertGreater(hi, config.resolve_threshold())


class RerankApply(unittest.TestCase):
    GATE = 0.45

    def test_low_cosine_passage_that_answers_wins(self):
        cands = [cand(0.47, text="near"), cand(0.38, layer="brain_raw", text="answer")]
        in_brain, conf, results = rerank.apply(cands, [], nouls(0.05, 0.91), self.GATE)
        self.assertEqual((in_brain, conf), (True, "high"))
        self.assertEqual([r["text"] for r in results], ["answer"])  # 0.05 < drop threshold
        self.assertEqual(results[0]["p_answers"], 0.91)

    def test_jev_can_reject_only_what_cosine_already_rated_weak(self):
        cands = [cand(0.44), cand(0.43), cand(0.42), cand(0.41)]   # above the 0.40 floor, below the gate
        in_brain, conf, results = rerank.apply(cands, [], nouls(0.2, 0.1, 0.1, 0.0), self.GATE)
        self.assertFalse(in_brain)
        self.assertEqual(len(results), 3)  # rejected candidates kept for transparency

    def test_jev_cannot_veto_a_decent_cosine_hit(self):
        cands = [cand(0.49, text="a"), cand(0.46, text="b")]
        in_brain, conf, results = rerank.apply(cands, [], nouls(0.1, 0.2), self.GATE)
        self.assertEqual((in_brain, conf), (True, "low"))
        self.assertEqual([r["text"] for r in results], ["a", "b"])  # cosine order, nothing pruned

    def test_jev_rescues_a_query_below_the_cosine_floor(self):
        in_brain, conf, _ = rerank.apply([cand(0.33)], [], nouls(0.8), self.GATE)
        self.assertEqual((in_brain, conf), (True, "high"))

    def test_rescue_below_the_floor_needs_a_clear_margin_because_jev_is_noisy(self):
        in_brain, _, _ = rerank.apply([cand(0.39)], [], nouls(0.55), self.GATE)   # measured noise band
        self.assertFalse(in_brain)
        in_brain, _, _ = rerank.apply([cand(0.41)], [], nouls(0.55), self.GATE)   # cosine already in-brain
        self.assertTrue(in_brain)

    def test_private_chunk_keeps_cosine_rule(self):
        private = [cand(0.55, src="wiki/pages/secret.md")]
        in_brain, conf, results = rerank.apply([cand(0.41)], private, nouls(0.01), self.GATE)
        self.assertEqual((in_brain, conf), (True, "low"))
        secret = [r for r in results if r["source_rel"] == "wiki/pages/secret.md"]
        self.assertEqual(len(secret), 1)
        self.assertNotIn("p_answers", secret[0])  # never judged, because never sent

    def test_missing_answer_raises_so_caller_falls_back(self):
        with self.assertRaises(jev.JevError):
            rerank.apply([cand(0.45), cand(0.44)], [], nouls(0.9), self.GATE)

    def test_request_numbers_passages_and_never_includes_private(self):
        with mock.patch.object(pages, "is_private", side_effect=lambda s: s.endswith("secret.md")), \
             mock.patch.object(jev, "ask", return_value=nouls(0.8)) as ask:
            in_brain, conf, results, stats = rerank.run("q?", [cand(0.45, text="PUBLIC"), cand(0.44, src="x/secret.md", text="HIDDEN")], [], self.GATE)
        state = ask.call_args.args[0]
        self.assertEqual(ask.call_args.kwargs["timeout"], config.JEV_QUERY_TIMEOUT_S)  # via ask_with_deadline
        self.assertIn("PUBLIC", state)
        self.assertNotIn("HIDDEN", state)
        self.assertEqual(stats, {"judged": 1, "withheld_private": 1})

    def test_private_best_match_leads_so_the_token_budget_cannot_cut_it(self):
        private = [cand(0.62, src="wiki/pages/secret.md", text="S"), cand(0.41, src="wiki/pages/secret2.md", text="S2")]
        _, _, results = rerank.apply([cand(0.50, text="a"), cand(0.48, text="b")], private, nouls(0.9, 0.8), self.GATE)
        self.assertEqual([r["text"] for r in results], ["S", "a", "b", "S2"])


class LinkOnlyFilter(unittest.TestCase):
    def test_cross_reference_chunks_are_dropped_and_content_kept(self):
        from scripts import query
        self.assertTrue(query._link_only("# Tenant Inventory\n## Cross-References\n\n- [[platform-architecture]]\n- [[call-history-schema]]\n"))
        self.assertTrue(query._link_only("## See also\n- [[a]] — [[b]]\n"))
        self.assertFalse(query._link_only("# Acme\n## Carrier\nCarrier is ExampleTel; TTS is ExampleVoice. See [[platform-architecture]]."))
        self.assertFalse(query._link_only("## Port\nProxy listens on 5433."))  # short facts are still content
        # regression: shell comments inside a code fence are body text, not headings
        self.assertFalse(query._link_only("# Arch\n## Deploy\n\n```bash\n# export env first\nmake deploy\n```\n"))


class JevGuards(unittest.TestCase):
    def test_privacy_strict_blocks_every_call(self):
        with mock.patch.object(config, "PRIVACY_STRICT", True):
            self.assertFalse(jev.enabled())
            with self.assertRaises(jev.JevError):
                jev.ask("s", {}, purpose="test")

    def test_disabled_flag_blocks_every_call(self):
        with mock.patch.object(config, "JEV_ENABLED", False):
            with self.assertRaises(jev.JevError):
                jev.ask("s", {}, purpose="test")


class Deadline(unittest.TestCase):
    def test_slow_server_cannot_hold_the_caller_past_the_deadline(self):
        import time
        def slow(*a, **k):
            time.sleep(2)
            return {}
        with mock.patch.object(jev, "ask", side_effect=slow), mock.patch.object(jev, "circuit_open", return_value=False), \
             mock.patch.object(jev, "_log"):
            t = time.perf_counter()
            with self.assertRaises(jev.JevError):
                jev.ask_with_deadline("s", {}, purpose="rerank", deadline=0.2)
            self.assertLess(time.perf_counter() - t, 0.6)

    def test_circuit_opens_after_consecutive_failures_and_closes_on_success_or_cooldown(self):
        import time
        now = time.time()
        bad = [(now - 30, False)] * config.JEV_BREAKER_FAILURES
        with mock.patch.object(jev, "_recent_outcomes", return_value=bad):
            self.assertTrue(jev.circuit_open("rerank"))
        with mock.patch.object(jev, "_recent_outcomes", return_value=bad[:-1] + [(now - 5, True)]):
            self.assertFalse(jev.circuit_open("rerank"))
        stale = [(now - config.JEV_BREAKER_COOLDOWN_S - 60, False)] * config.JEV_BREAKER_FAILURES
        with mock.patch.object(jev, "_recent_outcomes", return_value=stale):
            self.assertFalse(jev.circuit_open("rerank"))


class Frontmatter(unittest.TestCase):
    def test_inline_and_block_tag_lists(self):
        f, body = pages.parse_frontmatter("---\ntitle: X\ntags: [a, Private]\n---\n# X\n")
        self.assertEqual((f["title"], f["tags"], body), ("X", ["a", "Private"], "# X\n"))
        f, _ = pages.parse_frontmatter("---\ntags:\n  - one\n  - two\nlast_updated: 2026-09-01\n---\n")
        self.assertEqual((f["tags"], f["last_updated"]), (["one", "two"], "2026-09-01"))

    def test_no_frontmatter(self):
        self.assertEqual(pages.parse_frontmatter("# hi"), ({}, "# hi"))


class Staleness(unittest.TestCase):
    def test_review_interval_interpolates(self):
        t = config.STALENESS_REVIEW_DAYS
        self.assertEqual(staleness.review_days(0), t[0])
        self.assertEqual(staleness.review_days(3), t[3])
        self.assertEqual(staleness.review_days(2.5), round((t[2] + t[3]) / 2))
        self.assertEqual(staleness.review_days(9), t[3])   # clamped
        self.assertEqual(staleness.review_days(-1), t[0])

    def test_fast_page_goes_stale_before_flat_rule_and_evergreen_after(self):
        self.assertLess(staleness.review_days(3), config.STALENESS_FLAT_DAYS)
        self.assertGreater(staleness.review_days(0), config.STALENESS_FLAT_DAYS)


if __name__ == "__main__":
    unittest.main()

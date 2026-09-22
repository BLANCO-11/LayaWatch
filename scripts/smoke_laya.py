"""Smoke test: proves a local CPU deployment of laya actually works.

Exercises the two load paths and one routed request, then reports wall-clock
latency so CPU hosting expectations are grounded in measurement, not README claims.

  .venv/bin/python scripts/smoke_laya.py            # direct single-checkpoint load
  .venv/bin/python scripts/smoke_laya.py --router   # Router path (downloads both checkpoints)
"""
import sys
import time

STATE = {
    "from": "user@acme.com",
    "subject": "Duplicate charge on invoice #4411",
    "body": "Hi, we were billed twice for March. Please refund the duplicate today "
            "or we will cancel our plan.",
}
QUESTIONS = {
    "department": {
        "type": "choice",
        "instructions": "Which department should handle this request?",
        "criteria": {
            "billing": "invoices, payments, refunds",
            "technical": "bugs, outages, system errors",
            "sales": "pricing, new contracts",
            "other": "everything else",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this request?",
        "criteria": ["not urgent", "soon", "critical deadline or blocking issue"],
    },
    "churn_risk": {
        "type": "noul",
        "instructions": "Does the user threaten to cancel or leave?",
    },
}


def check(result, expect_choice=None):
    answers = result["answers"]
    assert set(answers) == set(QUESTIONS), answers.keys()
    dept = answers["department"]
    assert dept["choice"] in QUESTIONS["department"]["criteria"], dept
    assert 0.0 <= dept["confidence"] <= 1.0, dept
    noul = answers["churn_risk"]["noul"]
    assert 0.0 <= noul <= 1.0, noul
    if expect_choice:
        assert dept["choice"] == expect_choice, "wanted %r, got %r" % (expect_choice, dept["choice"])
    return dept, noul


def main():
    use_router = "--router" in sys.argv

    if use_router:
        from laya import Router
        t0 = time.perf_counter()
        router = Router(preload=["english", "multilingual"])
        load_s = time.perf_counter() - t0
        print("[1/3] preloaded english+multilingual in %.1fs" % load_s)

        t0 = time.perf_counter()
        res = router.predict(STATE, QUESTIONS)
        en_ms = (time.perf_counter() - t0) * 1000
        dept, noul = check(res, expect_choice="billing")
        assert res["routing"]["model"] == "english", res["routing"]
        print("[2/3] english routed OK in %.0f ms: dept=%s conf=%.2f churn=%.2f"
              % (en_ms, dept["choice"], dept["confidence"], noul))

        t0 = time.perf_counter()
        res_hi = router.predict({"body": "मुझसे दो बार शुल्क लिया गया, कृपया पैसे वापस करें।"}, QUESTIONS)
        hi_ms = (time.perf_counter() - t0) * 1000
        check(res_hi)
        assert res_hi["routing"]["model"] == "multilingual", res_hi["routing"]
        print("[3/3] hindi routed OK in %.0f ms -> %s (%s)"
              % (hi_ms, res_hi["routing"]["model"], res_hi["routing"]["reason"][:60]))
        print("PASS: router path healthy on this machine (cpu fp32, %.0f ms en / %.0f ms hi)" % (en_ms, hi_ms))
    else:
        import laya
        t0 = time.perf_counter()
        agent = laya.load("convaiinnovations/laya")
        load_s = time.perf_counter() - t0
        print("[1/3] english checkpoint loaded in %.1fs (device=%s)" % (load_s, agent.device))
        assert agent.device.type == "cpu", "this box has no GPU; expected cpu, got %s" % agent.device

        t0 = time.perf_counter()
        res = agent.predict(STATE, QUESTIONS)
        ms = (time.perf_counter() - t0) * 1000
        dept, noul = check(res, expect_choice="billing")
        print("[2/3] predict OK in %.0f ms: dept=%s conf=%.2f churn=%.2f"
              % (ms, dept["choice"], dept["confidence"], noul))

        t0 = time.perf_counter()
        agent.predict(STATE, QUESTIONS)
        ms2 = (time.perf_counter() - t0) * 1000
        print("[3/3] second predict OK in %.0f ms (warm)" % ms2)
        print("PASS: single-checkpoint CPU hosting works (load %.1fs, warm %.0f ms)" % (load_s, ms2))


if __name__ == "__main__":
    main()

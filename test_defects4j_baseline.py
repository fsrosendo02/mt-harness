from harness.adapters.defects4j import Defects4JAdapter
from harness.evaluators.baseline import BaselineEvaluator
from harness.models import Subject


def main():
    subject = Subject(
        dataset="defects4j",
        subject_id="Lang_1",
        language="java",
        version="f",
    )

    workdir = "./tmp/Lang_1_fixed"

    adapter = Defects4JAdapter()
    evaluator = BaselineEvaluator(adapter)

    print("Checking out subject...")
    adapter.checkout_subject(subject, workdir)

    print("Evaluating baseline...")
    result = evaluator.evaluate(subject, workdir)

    print("BUILD OK:", result.build_ok)
    print("TEST OK:", result.test_ok)
    print("\n=== BUILD LOG ===\n")
    print(result.build_log[:1000])
    print("\n=== TEST LOG ===\n")
    print(result.test_log[:1000])


if __name__ == "__main__":
    main()

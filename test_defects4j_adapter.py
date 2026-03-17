from harness.adapters.defects4j import Defects4JAdapter
from harness.models import Subject


def main():
    subject = Subject(
        dataset="defects4j",
        subject_id="Lang_1",
        language="java",
        version="f",
    )

    adapter = Defects4JAdapter()
    workdir = "./tmp/Lang_1"

    print("Checking out subject...")
    adapter.checkout_subject(subject, workdir)

    print("Compiling subject...")
    build_ok, build_log = adapter.build(workdir)
    print("BUILD OK:", build_ok)
    print(build_log[:1000])

    print("Running tests...")
    test_ok, test_log = adapter.test(workdir)
    print("TEST OK:", test_ok)
    print(test_log[:1000])


if __name__ == "__main__":
    main()
from harness.adapters.mock import MockAdapter
from harness.generators.dummy import DummyGenerator
from harness.models import Subject, Target
from harness.runner import ExperimentRunner


def main() -> None:
    subject = Subject(
        dataset="mock",
        subject_id="Mock_1",
        language="java",
    )

    target = Target(
        file_path="mock_source.java",
        function_name="isAlpha",
        start_line=1,
        end_line=11,
    )

    adapter = MockAdapter()
    generator = DummyGenerator()
    runner = ExperimentRunner(adapter, generator)

    workdir = "./tmp/mock_subject"
    run_dir = "./harness/runs/run_001"

    adapter.checkout_subject(subject, workdir)

    runner.run(
        subject=subject,
        target=target,
        num_mutants=3,
        workdir=workdir,
        run_dir=run_dir,
    )

    print(f"Run completed. Results saved in {run_dir}")


if __name__ == "__main__":
    main()

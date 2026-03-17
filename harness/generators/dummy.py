from harness.generators.base import MutantGenerator
from harness.models import Subject, Target, Mutant


class DummyGenerator(MutantGenerator):
    def generate(self, subject: Subject, target: Target, num_mutants: int) -> list[Mutant]:
        base_mutants = [
            """
public static boolean isAlpha(String str) {
    if (str == null) {
        return true;
    }
    int sz = str.length();
    for (int i = 0; i < sz; i++) {
        if (!Character.isLetter(str.charAt(i))) {
            return false;
        }
    }
    return true;
}
""".strip(),
            """
public static boolean isAlpha(String str) {
    if (str == null) {
        return false;
    }
    int sz = str.length();
    for (int i = 0; i < sz; i++) {
        if (Character.isLetter(str.charAt(i))) {
            return false;
        }
    }
    return true;
}
""".strip(),
            """
public static boolean isAlpha(String str) {
    if (str == null) {
        return false;
    }
    return true;
}
""".strip(),
        ]

        mutants = []
        for i in range(min(num_mutants, len(base_mutants))):
            mutants.append(
                Mutant(
                    mutant_id=f"m{i+1:02d}",
                    code=base_mutants[i],
                    source="dummy",
                    raw_response=None,
                )
            )

        return mutants

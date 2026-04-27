from pathlib import Path

from harness.adapters.defects4j import Defects4JAdapter
from harness.models import Subject, Target, Mutant
from harness.runners.mutation_runner import MutationRunner
from harness.executions.metadata import build_experiment_metadata
from harness.storage.layout import runs_root


def main():
    subject = Subject(
        dataset="defects4j",
        subject_id="Lang_1",
        language="java",
        version="f",
    )

    target = Target(
        file_path="src/main/java/org/apache/commons/lang3/math/NumberUtils.java",
        function_name="createNumber",
        start_line=450,
        end_line=623,
    )

    mutants = [
        Mutant(
            mutant_id="m_build_fail",
            source="manual",
            code="""
public static Number createNumber(final String str) throws NumberFormatException {
    THIS_WILL_NOT_COMPILE
}
""".strip(),
        ),
        Mutant(
            mutant_id="m_killed",
            source="manual",
            code="""
public static Number createNumber(final String str) throws NumberFormatException {
    if (str != null) {
        throw new NumberFormatException("forced mutant");
    }
    return null;
}
""".strip(),
        ),
        Mutant(
            mutant_id="m_survived",
            source="manual",
            code="""
    public static Number createNumber(final String str) throws NumberFormatException {
        if (str == null) {
            return null;
        }
        if (StringUtils.isBlank(str)) {
            throw new NumberFormatException("A blank string is not a valid number");
        }
        // Need to deal with all possible hex prefixes here
        final String[] hex_prefixes = {"0x", "0X", "-0x", "-0X", "#", "-#"};
        int pfxLen = 0;
        for(final String pfx : hex_prefixes) {
            if (str.startsWith(pfx)) {
                pfxLen += pfx.length();
                break;
            }
        }
        if (pfxLen > 0) { // we have a hex number
            char firstSigDigit = 0; // strip leading zeroes
            for(int i = pfxLen; i < str.length(); i++) {
                firstSigDigit = str.charAt(i);
                if (firstSigDigit == '0') { // count leading zeroes
                    pfxLen++;
                } else {
                    break;
                }
            }
            final int hexDigits = str.length() - pfxLen;
            if (hexDigits > 16 || (hexDigits == 16 && firstSigDigit > '7')) { // too many for Long
                return createBigInteger(str);
            }
            if (hexDigits > 8 || (hexDigits == 8 && firstSigDigit > '7')) { // too many for an int
                return createLong(str);
            }
            return createInteger(str);
        }
        final char lastChar = str.charAt(str.length() - 1);
        String mant;
        String dec;
        String exp;
        final int decPos = str.indexOf('.');
        final int expPos = str.indexOf('e') + str.indexOf('E') + 1; // assumes both not present
        // if both e and E are present, this is caught by the checks on expPos (which prevent IOOBE)
        // and the parsing which will detect if e or E appear in a number due to using the wrong offset

        int numDecimals = 0; // Check required precision (LANG-693)
        if (decPos > -1) { // there is a decimal point

            if (expPos > -1) { // there is an exponent
                if (expPos < decPos || expPos > str.length()) { // prevents double exponent causing IOOBE
                    throw new NumberFormatException(str + " is not a valid number.");
                }
                dec = str.substring(decPos + 1, expPos);
            } else {
                dec = str.substring(decPos + 1);
            }
            mant = str.substring(0, decPos);
            numDecimals = dec.length(); // gets number of digits past the decimal to ensure no loss of precision for floating point numbers.
        } else {
            if (expPos > -1) {
                if (expPos > str.length()) { // prevents double exponent causing IOOBE
                    throw new NumberFormatException(str + " is not a valid number.");
                }
                mant = str.substring(0, expPos);
            } else {
                mant = str;
            }
            dec = null;
        }
        if (!Character.isDigit(lastChar) && lastChar != '.') {
            if (expPos > -1 && expPos < str.length() - 1) {
                exp = str.substring(expPos + 1, str.length() - 1);
            } else {
                exp = null;
            }
            //Requesting a specific type..
            final String numeric = str.substring(0, str.length() - 1);
            final boolean allZeros = isAllZeros(mant) && isAllZeros(exp);
            switch (lastChar) {
                case 'l' :
                case 'L' :
                    if (dec == null
                        && exp == null
                        && (numeric.charAt(0) == '-' && isDigits(numeric.substring(1)) || isDigits(numeric))) {
                        try {
                            return createLong(numeric);
                        } catch (final NumberFormatException nfe) { // NOPMD
                            // Too big for a long
                        }
                        return createBigInteger(numeric);

                    }
                    throw new NumberFormatException(str + " is not a valid number.");
                case 'f' :
                case 'F' :
                    try {
                        final Float f = NumberUtils.createFloat(numeric);
                        if (!(f.isInfinite() || (f.floatValue() == 0.0F && !allZeros))) {
                            //If it's too big for a float or the float value = 0 and the string
                            //has non-zeros in it, then float does not have the precision we want
                            return f;
                        }

                    } catch (final NumberFormatException nfe) { // NOPMD
                        // ignore the bad number
                    }
                    //$FALL-THROUGH$
                case 'd' :
                case 'D' :
                    try {
                        final Double d = NumberUtils.createDouble(numeric);
                        if (!(d.isInfinite() || (d.floatValue() == 0.0D && !allZeros))) {
                            return d;
                        }
                    } catch (final NumberFormatException nfe) { // NOPMD
                        // ignore the bad number
                    }
                    try {
                        return createBigDecimal(numeric);
                    } catch (final NumberFormatException e) { // NOPMD
                        // ignore the bad number
                    }
                    //$FALL-THROUGH$
                default :
                    throw new NumberFormatException(str + " is not a valid number.");

            }
        }
        //User doesn't have a preference on the return type, so let's start
        //small and go from there...
        if (expPos > -1 && expPos < str.length() - 1) {
            exp = str.substring(expPos + 1, str.length());
        } else {
            exp = null;
        }
        if (dec == null && exp == null) { // no decimal point and no exponent
            //Must be an Integer, Long, Biginteger
            try {
                return createInteger(str);
            } catch (final NumberFormatException nfe) { // NOPMD
                // ignore the bad number
            }
            try {
                return createLong(str);
            } catch (final NumberFormatException nfe) { // NOPMD
                // ignore the bad number
            }
            return createBigInteger(str);
        }

        //Must be a Float, Double, BigDecimal
        final boolean allZeros = isAllZeros(mant) && isAllZeros(exp);
        try {
            if(numDecimals <= 7){// If number has 7 or fewer digits past the decimal point then make it a float
                final Float f = createFloat(str);
                if (!(f.isInfinite() || (f.floatValue() == 0.0F && !allZeros))) {
                    return f;
                }
            }
        } catch (final NumberFormatException nfe) { // NOPMD
            // ignore the bad number
        }
        try {
            if(numDecimals <= 16){// If number has between 8 and 16 digits past the decimal point then make it a double
                final Double d = createDouble(str);
                if (!(d.isInfinite() || (d.doubleValue() == 0.0D && !allZeros))) {
                    return d;
                }
            }
        } catch (final NumberFormatException nfe) { // NOPMD
            // ignore the bad number
        }

        return createBigDecimal(str);
    }

""".strip(),
        ),
    ]

    adapter = Defects4JAdapter()
    runner = MutationRunner(adapter)

    run_dir = runs_root() / "run_defects4j_smoke"
    workdir_base = "./tmp/Lang_1_multi"
    base_snapshot_dir = "./tmp/base_run_defects4j_smoke"

    print(f"Checking out subject into {base_snapshot_dir}")
    adapter.checkout_subject(subject, base_snapshot_dir)

    runner.run(
        subject=subject,
        target=target,
        mutants=mutants,
        run_dir=run_dir,
        workdir_base=workdir_base,
        base_snapshot_dir=base_snapshot_dir,
        run_mode="overwrite",
        extra_metadata=build_experiment_metadata(
            experiment_name="defects4j_lang1_manual_smoke",
            mutant_source="manual",
            model_name=None,
            model_provider="manual",
            prompt_name=None,
            prompt_version=None,
            temperature=None,
            n_requested_mutants=3,
            generation_mode="manual_control",
            dataset_split=None,
            notes="Smoke test with build-fail, killed, and survived control mutants.",
        ),
        cleanup_tmp=True,
        validate_after_run=True,
    )

    print("Done.")
    print(f"Results stored in: {run_dir}")


if __name__ == "__main__":
    main()

from pathlib import Path

from harness.adapters.defects4j import Defects4JAdapter
from harness.models import Subject, Target, Mutant
from harness.runners.mutation_runner import MutationRunner


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
            mutant_id="m01",
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
            pfxLen = pfx.length();
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
    final int expPos = str.indexOf('e') + str.indexOf('E') + 1;

    int numDecimals = 0;
    if (decPos > -1) {
        if (expPos > -1) {
            if (expPos < decPos || expPos > str.length()) {
                throw new NumberFormatException(str + " is not a valid number.");
            }
            dec = str.substring(decPos + 1, expPos);
        } else {
            dec = str.substring(decPos + 1);
        }
        mant = str.substring(0, decPos);
        numDecimals = dec.length();
    } else {
        if (expPos > -1) {
            if (expPos > str.length()) {
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
                    } catch (final NumberFormatException nfe) {
                    }
                    return createBigInteger(numeric);
                }
                throw new NumberFormatException(str + " is not a valid number.");
            case 'f' :
            case 'F' :
                try {
                    final Float f = NumberUtils.createFloat(numeric);
                    if (!(f.isInfinite() || (f.floatValue() == 0.0F && !allZeros))) {
                        return f;
                    }
                } catch (final NumberFormatException nfe) {
                }
            case 'd' :
            case 'D' :
                try {
                    final Double d = NumberUtils.createDouble(numeric);
                    if (!(d.isInfinite() || (d.floatValue() == 0.0D && !allZeros))) {
                        return d;
                    }
                } catch (final NumberFormatException nfe) {
                }
                try {
                    return createBigDecimal(numeric);
                } catch (final NumberFormatException e) {
                }
            default :
                throw new NumberFormatException(str + " is not a valid number.");
        }
    }
    if (expPos > -1 && expPos < str.length() - 1) {
        exp = str.substring(expPos + 1, str.length());
    } else {
        exp = null;
    }
    if (dec == null && exp == null) {
        try {
            return createInteger(str);
        } catch (final NumberFormatException nfe) {
        }
        try {
            return createLong(str);
        } catch (final NumberFormatException nfe) {
        }
        return createBigInteger(str);
    }

    final boolean allZeros = isAllZeros(mant) && isAllZeros(exp);
    try {
        if(numDecimals <= 7){
            final Float f = createFloat(str);
            if (!(f.isInfinite() || (f.floatValue() == 0.0F && !allZeros))) {
                return f;
            }
        }
    } catch (final NumberFormatException nfe) {
    }
    try {
        if(numDecimals <= 16){
            final Double d = createDouble(str);
            if (!(d.isInfinite() || (d.doubleValue() == 0.0D && !allZeros))) {
                return d;
            }
        }
    } catch (final NumberFormatException nfe) {
    }

    return createBigDecimal(str);
}
""".strip(),
        ),
    ]

    adapter = Defects4JAdapter()
    runner = MutationRunner(adapter)

    run_dir = Path("./harness/runs/run_defects4j_manual_multi")
    workdir_base = "./tmp/Lang_1_multi"

    runner.run(
        subject=subject,
        target=target,
        mutants=mutants,
        run_dir=str(run_dir),
        workdir_base=workdir_base,
    )

    print("Done.")
    print(f"Results stored in: {run_dir}")


if __name__ == "__main__":
    main()
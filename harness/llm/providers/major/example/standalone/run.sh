#!/bin/sh

MAJOR_HOME="../../"

echo "- Running javac without the mutation plugin"
echo "  (javac triangle/Triangle.java)"
javac triangle/Triangle.java

echo
echo "- Running javac with the major mutation plugin enabled"
echo "  (\$MAJOR_HOME/bin/major --mml \$MAJOR_HOME/mml/tutorial.mml.bin triangle/Triangle.java)"
$MAJOR_HOME/bin/major --mml $MAJOR_HOME/mml/tutorial.mml.bin triangle/Triangle.java

echo
echo "- Compiling test case (major-rt.jar has to be on the classpath!)"
echo "  (javac -cp .:\$MAJOR_HOME/lib/major-rt.jar TriangleTest.java)"
javac -cp .:$MAJOR_HOME/lib/major-rt.jar TriangleTest.java

echo
echo "- Executing test case (major-rt.jar has to be on the classpath!)"
echo "  (java -cp .:\$MAJOR_HOME/lib/major-rt.jar TriangleTest)"
echo
java -cp .:$MAJOR_HOME/lib/major-rt.jar TriangleTest

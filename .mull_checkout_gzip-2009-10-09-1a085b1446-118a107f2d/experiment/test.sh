#!/bin/bash
bugrev=1a085b1446

#Check if coverage is being run. If so, don't use time limit.
if [ `basename $2` = "coverage" ] ; then
    cov=0
else
    cov=1
fi

run_test()
{
    cd src
    if [ $cov -eq 0 ] ; then
        perl /experiment/gzip-run-tests.pl $1
    else
        timeout 5 perl /experiment/gzip-run-tests.pl $1
    fi
    RESULT=$?
    
    cd ..
    return $RESULT
}
case $1 in
    p1) run_test 1 && exit 0 ;; 
    p2) run_test 3 && exit 0 ;; 
    p3) run_test 4 && exit 0 ;; 
    p4) run_test 7 && exit 0 ;; 
    p5) run_test 9 && exit 0 ;; 
    n1) run_test 8 && exit 0 ;; 
esac
exit 1
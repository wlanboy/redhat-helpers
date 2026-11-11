#!/usr/bin/env bash
# hdfs-acl add-client2: 78 Pfade, erzeugt 2026-11-11T08:21:41
# geplant auf: fsimage_0000000000000001017 (Datei vom 2026-11-11T08:15:52)
# zurückdrehen: uv run main.py restore /home/samuel/git/redhat-helpers/hdfs/hdfs-acl-backup-20261111-082141-add-client2.csv --apply

# Hadoop-Client: per HADOOP überschreibbar, z. B. HADOOP=/opt/hadoop/bin/hadoop
HADOOP="${HADOOP:-hadoop}"
failed=0

# alt: @client1|@client2=3,svc_backup=1,@client3=1,#ops=3
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client2=19,svc_backup=1,@client3=1,#ops=3"' /fs/account1 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container1 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container2 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container6 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container7 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container11 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container12 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container16 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container17 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container21 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container22 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container26 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container27 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container31 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container32 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container36 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container37 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container41 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container42 || failed=$((failed + 1))
# alt: @client1|@client4=3,dev_user=3,@client5=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client4=3,dev_user=3,@client5=1,@client2=19"' /fs/account1/container46 || failed=$((failed + 1))
# alt: @client6|#readers=1,@client6=1,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client6|#readers=1,@client6=1,@client1=1,@client2=1"' /fs/account1/container47 || failed=$((failed + 1))
# alt: @client1|@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client2=19"' /fs/account1/container52 || failed=$((failed + 1))
# alt: @client1|@client3=5,#ops=16,@client3=16,@client1=8,cn=doc,ou=hell,dc=firma=3,cn=testgroup
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client3=5,#ops=16,@client3=16,@client1=8,cn=doc,ou=hell,dc=firma=3,cn=testgroup,@client2=27"' /fs/account2/container48 || failed=$((failed + 1))
# alt: @client1|
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client2=19"' /fs/account3/container49 || failed=$((failed + 1))
# alt: maria|@client10=3,@client1=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client10=3,@client1=1,#ops=1,@client2=1"' /fs/account4/container3 || failed=$((failed + 1))
# alt: maria|@client10=3,@client1=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client10=3,@client1=1,#ops=1,@client2=1"' /fs/account4/container8 || failed=$((failed + 1))
# alt: maria|@client10=3,@client1=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client10=3,@client1=1,#ops=1,@client2=1"' /fs/account4/container13 || failed=$((failed + 1))
# alt: maria|@client10=3,@client1=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client10=3,@client1=1,#ops=1,@client2=1"' /fs/account4/container18 || failed=$((failed + 1))
# alt: maria|@client10=3,@client1=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client10=3,@client1=1,#ops=1,@client2=1"' /fs/account4/container23 || failed=$((failed + 1))
# alt: maria|@client10=3,@client1=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client10=3,@client1=1,#ops=1,@client2=1"' /fs/account4/container28 || failed=$((failed + 1))
# alt: maria|@client10=3,@client1=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client10=3,@client1=1,#ops=1,@client2=1"' /fs/account4/container33 || failed=$((failed + 1))
# alt: maria|@client10=3,@client1=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client10=3,@client1=1,#ops=1,@client2=1"' /fs/account4/container38 || failed=$((failed + 1))
# alt: maria|@client10=3,@client1=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client10=3,@client1=1,#ops=1,@client2=1"' /fs/account4/container43 || failed=$((failed + 1))
# alt: maria|@client1=3,@client2=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client1=3,@client2=3,#ops=1"' /fs/account5/container3 || failed=$((failed + 1))
# alt: maria|@client1=3,@client2=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client1=3,@client2=3,#ops=1"' /fs/account5/container8 || failed=$((failed + 1))
# alt: maria|@client1=3,@client2=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client1=3,@client2=3,#ops=1"' /fs/account5/container13 || failed=$((failed + 1))
# alt: maria|@client1=3,@client2=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client1=3,@client2=3,#ops=1"' /fs/account5/container18 || failed=$((failed + 1))
# alt: maria|@client1=3,@client2=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client1=3,@client2=3,#ops=1"' /fs/account5/container23 || failed=$((failed + 1))
# alt: maria|@client1=3,@client2=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client1=3,@client2=3,#ops=1"' /fs/account5/container28 || failed=$((failed + 1))
# alt: maria|@client1=3,@client2=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client1=3,@client2=3,#ops=1"' /fs/account5/container33 || failed=$((failed + 1))
# alt: maria|@client1=3,@client2=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client1=3,@client2=3,#ops=1"' /fs/account5/container38 || failed=$((failed + 1))
# alt: maria|@client1=3,@client2=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@client1=3,@client2=3,#ops=1"' /fs/account5/container43 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container2 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container7 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container12 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container17 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container22 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container27 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container32 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container37 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container42 || failed=$((failed + 1))
# alt: @client1|#readers=1,@client1=1,@client6=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|#readers=1,@client1=1,@client6=1,@client2=19"' /fs/account6/container47 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container1 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container6 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container11 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container16 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container21 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container26 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container31 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container36 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container41 || failed=$((failed + 1))
# alt: @client7|@client10=3,dev_user=3,@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client7|@client10=3,dev_user=3,@client1=1,@client2=1"' /fs/account7/container46 || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container1 || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container6 || failed=$((failed + 1))
# alt: @client10|@client1=1,#ops=3
"$HADOOP" fs -setfattr -n user.acl -v '"@client10|@client1=1,#ops=3,@client2=1"' /fs/account8/container10/sub || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container11 || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container16 || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container21 || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container26 || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container31 || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container36 || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container41 || failed=$((failed + 1))
# alt: @client8|@client1=3,dev_user=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client8|@client1=3,dev_user=3,@client2=3"' /fs/account8/container46 || failed=$((failed + 1))
# alt: @client9|@client10=3,svc_backup=1,@client1=1,#ops=3
"$HADOOP" fs -setfattr -n user.acl -v '"@client9|@client10=3,svc_backup=1,@client1=1,#ops=3,@client2=1"' /fs/account9 || failed=$((failed + 1))
# alt: @client1|@client2=1,#ops=3
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client2=19,#ops=3"' /fs/account9/container10/sub || failed=$((failed + 1))
# alt: @client9|@client1=5,#ops=16,@client1=16,@client9=8,cn=doc,ou=hell,dc=firma=3,cn=testgroup
"$HADOOP" fs -setfattr -n user.acl -v '"@client9|@client1=5,#ops=16,@client1=16,@client9=8,cn=doc,ou=hell,dc=firma=3,cn=testgroup,@client2=21"' /fs/account10/container48 || failed=$((failed + 1))
# alt: @client1|@client10=1,#ops=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client1|@client10=1,#ops=1,@client2=19"' /fs/account10/container51 || failed=$((failed + 1))
# alt: @client10|@client1=1
"$HADOOP" fs -setfattr -n user.acl -v '"@client10|@client1=1,@client2=1"' /fs/account10/container52 || failed=$((failed + 1))

if [ "$failed" -ne 0 ]; then
    echo "$failed von 78 Aufrufen fehlgeschlagen" >&2
    exit 1
fi
echo "OK: 78 Pfade geschrieben"

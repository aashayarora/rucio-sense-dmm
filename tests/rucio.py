import psycopg2
import argparse

HOST = ""
DBNAME = ""
USER = ""
PASSWORD = ""

# Add a rule to the requests table
def add_rule(rule_id, src_site, dst_site, priority):
    conn = psycopg2.connect(f"host={HOST} dbname={DBNAME} user={USER} password={PASSWORD}")
    cur = conn.cursor()
    cur.execute(f"INSERT INTO requests (rule_id, src_site, dst_site, priority, transfer_status) VALUES ('{rule_id}', '{src_site}', '{dst_site}', '{priority}', 'INIT');")
    conn.commit()
    conn.close()

# mark rule as modified, change priority and transfer_status to MODIFIED
def modify_rule(rule_id, priority):
    conn = psycopg2.connect(f"host={HOST} dbname={DBNAME} user={USER} password={PASSWORD}")
    cur = conn.cursor()
    cur.execute(f"UPDATE requests SET priority='{priority}', modified_priority='{priority}', transfer_status='MODIFIED' WHERE rule_id='{rule_id}';")
    conn.commit()
    conn.close()

# mark rule as finished
def finish_rule(rule_id):
    conn = psycopg2.connect(f"host={HOST} dbname={DBNAME} user={USER} password={PASSWORD}")
    cur = conn.cursor()
    cur.execute(f"UPDATE requests SET transfer_status='FINISHED' WHERE rule_id='{rule_id}';")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="Fake Rucio Requests to DMM")
    # collect action type, based on option selected, perform the action and require other args
    argparser.add_argument("action", help="Action to perform: add, finish, modify")
    argparser.add_argument("--rule_id", help="Rule ID")
    argparser.add_argument("--src_site", help="Source Site")
    argparser.add_argument("--dst_site", help="Destination Site")
    argparser.add_argument("--priority", help="Priority")
    args = argparser.parse_args()

    if args.action == "add":
        if args.rule_id is None or args.src_site is None or args.dst_site is None or args.priority is None:
            print("Missing required arguments: rule_id, src_site, dst_site, priority")
            exit(1)
        add_rule(args.rule_id, args.src_site, args.dst_site, args.priority)
        print(f"Rule {args.rule_id} added")
    elif args.action == "modify":
        if args.rule_id is None or args.priority is None:
            print("Missing required arguments: rule_id, priority")
            exit(1)
        modify_rule(args.rule_id, args.priority)
        print(f"Rule {args.rule_id} modified")
    elif args.action == "finish":
        if args.rule_id is None:
            print("Missing required arguments: rule_id")
            exit(1)
        finish_rule(args.rule_id)
        print(f"Rule {args.rule_id} marked as FINISHED")
    else:
        print("Invalid action")
        exit(1)
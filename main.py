def run_scan():
    print("=== MoneyRadar Agent – starting scan ===")

    seen_ids = load_seen_posts()
    print(f"Loaded {len(seen_ids)} previously-seen post IDs.")

    reddit_posts = []  # Reddit disabled — no API access
    x_posts = scan_x()
    print(f"Fetched {len(x_posts)} posts from X.")

    all_posts = reddit_posts + x_posts
    new_alerts = 0
    today_str = date.today().isoformat()

    for post in all_posts:
        if post["id"] in seen_ids:
            continue  # already alerted on this one

        seen_ids.add(post["id"])  # mark as seen regardless of pass/fail — don't re-evaluate it next run

        result = evaluate_post(post["title"], post["body"])
        if not result["passes"]:
            continue  # silently skip — no need to log every non-match

        # Same-day author dedupe: skip if this author already triggered an alert today.
        author = post.get("author")
        if author:
            author_key = f"authorday_{author}_{today_str}"
            if author_key in seen_ids:
                print(f"⏭️  SKIPPED (already alerted this author today): {post['title'][:60]}")
                continue
            seen_ids.add(author_key)

        # AI verification: catches vague hype / airdrop-farming pitches / expired
        # offers that keyword-matching alone can't distinguish from a real offer.
        verified, ai_reason = verify_opportunity(post["title"], post["body"])
        if not verified:
            print(f"🤖 AI REJECTED: {post['title'][:60]} — {ai_reason}")
            continue

        print(f"✅ MATCH: {post['title'][:80]} ({result['matched_amount']})")
        success = send_alert(post, result["matched_amount"])
        if success:
            new_alerts += 1

    save_seen_posts(seen_ids)
    send_summary(new_alerts, len(all_posts))

    print(f"=== Scan complete. {new_alerts} new alert(s) sent out of {len(all_posts)} posts scanned. ===")


if __name__ == "__main__":
    run_scan()

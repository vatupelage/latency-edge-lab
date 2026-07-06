import asyncio, json, time, requests
import websockets

GAMMA="https://gamma-api.polymarket.com/events"
WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"

def cur_slug():
    ws=(int(time.time())//300)*300
    return f"btc-updown-5m-{ws}", ws+300

def resolve(slug):
    r=requests.get(f"{GAMMA}?slug={slug}",timeout=6).json()
    mkt=r[0]["markets"][0]
    toks=json.loads(mkt["clobTokenIds"])
    outs=json.loads(mkt.get("outcomes",'["Up","Down"]'))
    up_idx=0 if outs[0].lower().startswith("up") else 1
    return toks[up_idx], toks[1-up_idx]

async def main():
    slug,end=cur_slug()
    up,down=resolve(slug)
    print("slug",slug,"end_in",int(end-time.time()),"s")
    print("up_token",up[:18],"down_token",down[:18])
    seen={"book":0,"price_change":0,"other":0}
    bid_seen_snapshot=False; bid_seen_delta=False; sample_book=None; sample_pc=None
    async with websockets.connect(WS,ping_interval=10,close_timeout=3,compression=None,max_size=2**20) as w:
        await w.send(json.dumps({"type":"market","assets_ids":[up,down]}))
        t0=time.time()
        while time.time()-t0 < 12:
            try: raw=await asyncio.wait_for(w.recv(),timeout=12)
            except asyncio.TimeoutError: break
            msgs=json.loads(raw)
            if isinstance(msgs,dict): msgs=[msgs]
            for m in msgs:
                et=m.get("event_type")
                if et=="book":
                    seen["book"]+=1
                    bids=m.get("bids") or []
                    if bids: bid_seen_snapshot=True
                    if sample_book is None:
                        sample_book={"asset":m.get("asset_id","")[:12],"n_bids":len(bids),"n_asks":len(m.get("asks") or []),"top_bid":bids[0] if bids else None,"top_ask":(m.get("asks") or [None])[0]}
                elif et=="price_change":
                    seen["price_change"]+=1
                    for ch in m.get("price_changes") or []:
                        if str(ch.get("side","")).lower() in ("buy","bid"): bid_seen_delta=True
                    if sample_pc is None and m.get("price_changes"):
                        sample_pc=m["price_changes"][0]
                else: seen["other"]+=1
    print("events:",seen)
    print("bids in snapshot?",bid_seen_snapshot," bids in deltas?",bid_seen_delta)
    print("sample book:",sample_book)
    print("sample price_change:",sample_pc)

asyncio.run(main())

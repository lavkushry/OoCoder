"""Localhost sponsor UI: factory board + approval queue."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from oss_agent.config import AppConfig
from oss_agent.db.models import Action, Factory, WorkOrder
from oss_agent.db.session import create_all, make_engine, session_scope
from oss_agent.factory.board import BoardLane, group_by_lane

app = FastAPI(title="oss-agent", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    cfg = AppConfig()
    engine = make_engine(cfg.database_url)
    create_all(engine)
    board_html = ""
    queue_rows: list[str] = []
    with session_scope(engine) as session:
        factory = session.scalars(select(Factory).limit(1)).first()
        if factory:
            orders = list(
                session.scalars(select(WorkOrder).where(WorkOrder.factory_id == factory.id)).all()
            )
            # eager-ish: access executions
            for o in orders:
                _ = list(o.executions)
            board = group_by_lane(orders)
            cols = []
            for lane in BoardLane:
                cards = (
                    "".join(
                        f"<div class='card'><strong>#{o.number}</strong> {o.title}"
                        f"<br/><small>{o.state}</small></div>"
                        for o in board.get(lane, [])
                    )
                    or "<div class='empty'>—</div>"
                )
                cols.append(f"<section><h2>{lane}</h2>{cards}</section>")
            board_html = f"<h1>{factory.name}</h1><div class='board'>{''.join(cols)}</div>"
        for action in session.scalars(select(Action).where(Action.status == "pending")):
            queue_rows.append(
                f"<li><code>{action.id}</code> {action.kind} task={action.task_id}</li>"
            )
    body = "".join(queue_rows) or "<li>no pending drafts</li>"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/>
<title>oss-agent factory</title>
<style>
  body {{ font-family: ui-sans-serif, system-ui; margin: 1.5rem;
    background: #f6f4ef; color: #1a1a1a; }}
  .board {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }}
  section {{ background: #fff; border: 1px solid #ddd;
    padding: 0.75rem; min-height: 12rem; }}
  h1,h2 {{ font-weight: 600; }}
  .card {{ border: 1px solid #ccc; padding: 0.5rem; margin: 0.5rem 0; background: #fafafa; }}
  .empty {{ color: #888; }}
</style></head><body>
{board_html or "<h1>oss-agent factory</h1><p>No factory yet — run <code>make demo</code>.</p>"}
<h2>Approval queue</h2>
<p>Kill switch: {cfg.policy.kill_switch}</p>
<ul>{body}</ul>
<p>Approve via CLI: <code>oss-agent approve &lt;id&gt;</code></p>
</body></html>"""

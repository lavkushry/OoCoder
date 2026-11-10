"""Typer CLI: version, approval queue, kill switch, serve, demo."""

from __future__ import annotations

from pathlib import Path

import typer
from sqlalchemy import select

from oss_agent.config import AppConfig
from oss_agent.db.models import Action
from oss_agent.db.session import create_all, make_engine, session_scope

app = typer.Typer(name="oss-agent", help="Self-hosted OSS contribution agent")


@app.command()
def version() -> None:
    """Print package version."""
    from oss_agent import __version__

    typer.echo(__version__)


@app.command("queue")
def approval_queue(
    db_url: str = typer.Option("", help="SQLAlchemy URL; default from config"),
) -> None:
    """List pending outward action drafts."""
    cfg = AppConfig()
    url = db_url or cfg.database_url
    engine = make_engine(url)
    create_all(engine)
    with session_scope(engine) as session:
        rows = session.scalars(select(Action).where(Action.status == "pending")).all()
        if not rows:
            typer.echo("no pending drafts")
            return
        for row in rows:
            typer.echo(f"{row.id}\t{row.kind}\ttask={row.task_id}\t{row.draft}")


@app.command()
def approve(
    draft_id: str,
    by: str = typer.Option("sponsor", "--by"),
    db_url: str = typer.Option(""),
) -> None:
    """Approve and send a pending draft through the gateway."""
    from oss_agent.audit.log import FileAndDbAuditLog
    from oss_agent.gateway.service import GitHubGatewayService

    cfg = AppConfig()
    url = db_url or cfg.database_url
    engine = make_engine(url)
    create_all(engine)
    with session_scope(engine) as session:
        audit = FileAndDbAuditLog(Path(cfg.audit_dir) / "audit.jsonl", session)
        gw = GitHubGatewayService(session, cfg, audit)
        result = gw._mark_and_maybe_send(draft_id, approved_by=by)
        typer.echo(f"sent ok={result.ok} remote_id={result.remote_id}")


@app.command("kill-switch")
def kill_switch(
    enable: bool = typer.Option(True, "--enable/--disable"),
) -> None:
    """Print how to flip the kill switch via env (config is process-local)."""
    if enable:
        typer.echo("export OSS_AGENT_POLICY__KILL_SWITCH=true")
    else:
        typer.echo("export OSS_AGENT_POLICY__KILL_SWITCH=false")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8787),
) -> None:
    """Start localhost UI."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        typer.echo("host must be localhost")
        raise typer.Exit(code=2)
    import uvicorn

    from oss_agent.web.app import app as web_app

    uvicorn.run(web_app, host=host, port=port, log_level="info")


@app.command()
def demo(offline: bool = typer.Option(True, "--offline/--online")) -> None:
    """Offline fixture demo via SuperPlane-style factory board."""
    if not offline:
        typer.echo("online demo disabled until gateway credentials exist")
        raise typer.Exit(code=2)
    from oss_agent.demo import run_offline_demo

    run_offline_demo()


factory_app = typer.Typer(help="Software factory (SuperPlane-inspired) commands")
app.add_typer(factory_app, name="factory")


@factory_app.command("board")
def factory_board(db_url: str = typer.Option("")) -> None:
    """Print work orders grouped into Backlog / Running / Review / Done."""
    from oss_agent.db.models import Factory
    from oss_agent.factory.board import group_by_lane
    from oss_agent.factory.service import FactoryService

    cfg = AppConfig()
    engine = make_engine(db_url or cfg.database_url)
    create_all(engine)
    with session_scope(engine) as session:
        factory = session.scalars(select(Factory).limit(1)).first()
        if factory is None:
            typer.echo("no factory — run: oss-agent demo --offline")
            raise typer.Exit(code=1)
        svc = FactoryService(session)
        orders = svc.list_orders(factory.id)
        for o in orders:
            _ = list(o.executions)
        board = group_by_lane(orders)
        typer.echo(f"Factory: {factory.name} ({factory.key})")
        for lane, items in board.items():
            typer.echo(f"[{lane}]")
            for order in items:
                typer.echo(f"  #{order.number} {order.title} ({order.state})")


@factory_app.command("close")
def factory_close(
    order_id: str,
    result: str = typer.Option("completed", "--result"),
    db_url: str = typer.Option(""),
) -> None:
    """Close a work order (completed|rejected|failed)."""
    from oss_agent.factory.service import FactoryService
    from oss_agent.factory.states import CloseResult, WorkOrderState

    cfg = AppConfig()
    engine = make_engine(db_url or cfg.database_url)
    create_all(engine)
    with session_scope(engine) as session:
        svc = FactoryService(session)
        order = svc.update_status(
            order_id,
            state=WorkOrderState.CLOSED,
            result=CloseResult(result),
            actor="sponsor",
        )
        typer.echo(f"closed {order.id} result={order.result}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

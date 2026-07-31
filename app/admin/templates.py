"""
Plain Python f-string HTML generation, deliberately not Jinja2. Jinja2
itself is pure Python, but its MarkupSafe dependency ships an optional C
speedup extension -- MarkupSafe has a documented pure-Python fallback if
that extension fails to build (same safe-optional-compile story as
SQLAlchemy's own C accelerator), so it's not a hard Termux blocker the
way pydantic-core was, but it's still a dependency this project doesn't
need for what's a genuinely small amount of HTML. Every value interpolated
from user/DB data goes through html.escape() -- see esc() below -- since
there's no templating engine auto-escaping for us here.
"""
import html


def esc(value) -> str:
    return html.escape(str(value), quote=True)


PAGE_SHELL = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: 0.3rem; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 0.5rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #eee; font-size: 0.9rem; }}
  th {{ color: #666; font-weight: 600; }}
  form.inline {{ display: inline; }}
  .card {{ background: #fafafa; border: 1px solid #eee; border-radius: 6px; padding: 1rem; margin-top: 0.75rem; }}
  input, select, button {{ padding: 0.4rem 0.6rem; font-size: 0.9rem; margin-right: 0.4rem; }}
  button {{ cursor: pointer; }}
  .error {{ color: #b00020; background: #fdecea; padding: 0.6rem; border-radius: 4px; }}
  .muted {{ color: #888; font-size: 0.85rem; }}
  a {{ color: #0b57d0; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def render_login_page(error: str | None = None) -> str:
    error_html = f'<p class="error">{esc(error)}</p>' if error else ""
    body = f"""
<h1>SAEOS Admin</h1>
{error_html}
<form method="post" action="/admin/login" class="card">
  <div><label>Tenant slug<br><input name="tenant_slug" required></label></div>
  <div style="margin-top:0.5rem"><label>Email<br><input name="email" type="email" required></label></div>
  <div style="margin-top:0.5rem"><label>Password<br><input name="password" type="password" required></label></div>
  <div style="margin-top:0.75rem"><button type="submit">Log in</button></div>
</form>
"""
    return PAGE_SHELL.format(title="SAEOS Admin — Log in", body=body)


def render_dashboard(tenant_name: str, roles: list[dict], permissions: list[dict], users: list[dict]) -> str:
    permission_options = "".join(f'<option value="{esc(p["code"])}">{esc(p["code"])}</option>' for p in permissions)

    roles_rows = "".join(
        f"""<tr>
          <td>{esc(r['name'])}</td>
          <td>{esc(', '.join(r['permissions']) or '—')}</td>
          <td>
            <form class="inline" method="post" action="/admin/roles/{esc(r['id'])}/permissions">
              <select name="permission_code">{permission_options}</select>
              <button type="submit">Add permission</button>
            </form>
          </td>
        </tr>"""
        for r in roles
    )

    role_options = "".join(f'<option value="{esc(r["id"])}">{esc(r["name"])}</option>' for r in roles)
    users_rows = "".join(
        f"""<tr>
          <td>{esc(u['email'])}{' <span class="muted">(owner)</span>' if u['is_tenant_owner'] else ''}</td>
          <td>{esc(', '.join(u['roles']) or '—')}</td>
          <td>
            {'' if u['is_tenant_owner'] else f'''<form class="inline" method="post" action="/admin/users/{esc(u["id"])}/roles">
              <select name="role_id">{role_options}</select>
              <button type="submit">Assign role</button>
            </form>'''}
          </td>
        </tr>"""
        for u in users
    )

    body = f"""
<h1>{esc(tenant_name)} — Admin</h1>
<form method="post" action="/admin/logout" class="inline"><button type="submit">Log out</button></form>

<h2>Roles</h2>
<div class="card">
  <form method="post" action="/admin/roles/create">
    <input name="name" placeholder="New role name" required>
    <button type="submit">Create role</button>
  </form>
</div>
<table>
  <tr><th>Role</th><th>Permissions</th><th></th></tr>
  {roles_rows or '<tr><td colspan="3" class="muted">No roles yet</td></tr>'}
</table>

<h2>Users</h2>
<table>
  <tr><th>Email</th><th>Roles</th><th></th></tr>
  {users_rows}
</table>

<h2>Permission catalog</h2>
<table>
  <tr><th>Code</th><th>Description</th></tr>
  {"".join(f"<tr><td>{esc(p['code'])}</td><td class='muted'>{esc(p['description'])}</td></tr>" for p in permissions)}
</table>
"""
    return PAGE_SHELL.format(title=f"{esc(tenant_name)} — SAEOS Admin", body=body)

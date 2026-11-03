#!/usr/bin/env python3
"""The Owl half of the series seam, checked (docs/plans/multi-version.md).

Owl 3 (Odoo 20) throws on `static props`, hands a component its props only
through `useProps()`, has no `useState`, `useRef` or `useEffect` of Owl 2's
shape, and resolves a bare name in a template expression to a template local
(a `t-as` or `t-set` name) and nothing else: not the component, not a global
(`Number(x)` compiles to `ctx['Number'](x)`). The suite cannot see any of it,
the Python is green and the widget draws nothing. Measured 2026-11-03: the
up-and-running line landed on main with all three and never appeared on 20.

What this checks, in pan_mail_pro/static/src and the series overlays:
- no `static props =` on a component: the schema is `static propsSchema`,
  with `defineProps(Class)` after the class
- `useState`, `useRef`, `useEffect`, `useSubEnv`, `useProps` and `defineProps`
  come from `@pan_mail_pro/compat/owl`, never from `@odoo/owl` (the compat
  files are where that import belongs)
- every name in a template expression is `this.`-prefixed, a template local,
  an arrow parameter, a literal or a keyword

Exit 1 with one `::error::` line per finding.
"""
import glob
import re
import sys
from xml.etree import ElementTree

SEAM = {'useState', 'useRef', 'useEffect', 'useSubEnv', 'useProps', 'defineProps'}
# Owl's own `and`, `or` and `not` included.
KEYWORDS = {'this', 'true', 'false', 'null', 'undefined', 'typeof', 'new', 'in',
            'of', 'instanceof', 'void', 'and', 'or', 'not'}
# Attributes whose value is a name or a plain string, not an expression.
NOT_EXPRESSIONS = {'t-name', 't-inherit', 't-inherit-mode', 't-call', 't-as',
                   't-set', 't-slot-scope', 't-translation', 't-call-slot',
                   't-slot', 't-set-slot', 't-portal', 't-model'}
STRING = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`")
IDENT = re.compile(r'(?<![\w$.])([A-Za-z_$][\w$]*)')
KEY = re.compile(r'(?:^|[{,])\s*([A-Za-z_$][\w$]*)\s*:')
ARROW = re.compile(r'(?:\(([^()]*)\)|([A-Za-z_$][\w$]*))\s*=>')
FORMAT = re.compile(r'\{\{(.*?)\}\}', re.S)


def js_files():
    for pattern in ('pan_mail_pro/static/src/**/*.js',
                    'tools/series/*/pan_mail_pro/static/src/**/*.js'):
        yield from glob.glob(pattern, recursive=True)


def xml_files():
    for pattern in ('pan_mail_pro/static/src/**/*.xml',
                    'tools/series/*/pan_mail_pro/static/src/**/*.xml'):
        yield from glob.glob(pattern, recursive=True)


def check_js(path, source, errors):
    if '/compat/' in path:
        return
    for number, line in enumerate(source.splitlines(), 1):
        if re.search(r'\bstatic\s+props\s*=', line):
            errors.append(f'{path}:{number}: `static props` throws on Owl 3; '
                          'use `static propsSchema` and defineProps(Class)')
    for match in re.finditer(r'import\s*\{([^}]*)\}\s*from\s*"@odoo/owl"', source):
        names = {n.strip().split(' as ')[0] for n in match.group(1).split(',')}
        for name in sorted(names & SEAM):
            errors.append(f'{path}: `{name}` is imported from @odoo/owl; '
                          'import it from @pan_mail_pro/compat/owl')


def locals_of(root):
    """Every `t-as`, `t-set` and `t-slot-scope` name in the file: a template
    `t-call`ed from another sees the caller's locals, so the file is the
    scope that can be checked without following the calls."""
    names = set()
    for element in root.iter():
        for attr in ('t-as', 't-set', 't-slot-scope'):
            name = element.get(attr)
            if name:
                names.add(name)
                if attr == 't-as':
                    names.update(f'{name}_{s}' for s in ('index', 'value', 'first', 'last'))
    return names


def names_in(expression, local_names):
    stripped = STRING.sub("''", expression)
    allowed = set(local_names)
    for params, single in ARROW.findall(stripped):
        allowed.update(p.strip() for p in (params or single).split(',') if p.strip())
    keys = set(KEY.findall(stripped))
    for name in IDENT.findall(stripped):
        if name in KEYWORDS or name in allowed or name in keys:
            continue
        yield name


def check_xml(path, errors):
    root = ElementTree.parse(path).getroot()
    local_names = locals_of(root)
    for template in root:
        for element in template.iter():
            for attr, value in element.attrib.items():
                if not attr.startswith('t-') or attr in NOT_EXPRESSIONS:
                    continue
                expressions = FORMAT.findall(value) if attr.startswith('t-attf-') else [value]
                for expression in expressions:
                    for name in names_in(expression, local_names):
                        errors.append(
                            f'{path}: {template.get("t-name") or template.get("t-inherit")}: '
                            f'`{name}` in {attr}="{value}" is not this., a local or a literal')


def main():
    errors = []
    for path in sorted(js_files()):
        with open(path, encoding='utf-8') as handle:
            check_js(path, handle.read(), errors)
    for path in sorted(xml_files()):
        check_xml(path, errors)
    for line in errors:
        print(f'::error::{line}')
    if errors:
        return 1
    print('OK: every component and template is on the Owl seam.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

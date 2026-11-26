// Reading units (definitions) and call sites out of a syntax tree, per language
// (ADR-0017, slice 10). Traversal uses explicit stacks, never recursion over the tree:
// minified code gives trees deep enough to overflow the JavaScript stack.

import type { Node } from 'web-tree-sitter';

import type { CallSite, Unit } from './chunkPlan';
import type { LanguageId } from './grammars';

interface Found {
  readonly kind: string;
  readonly name: string;
  /** Where nested units are looked for when the unit is split (methods, subsections). */
  readonly body: Node | null;
}

interface Spec {
  readonly prefix: string;
  /** The unit this node is, if any. */
  readonly unit: (node: Node) => Found | null;
  /** The name a call or construction names, if this node is one. */
  readonly call?: (node: Node) => string | null;
  /** Units from the root's children, for languages whose units are not nodes. */
  readonly top?: (root: Node) => Unit[];
}

const field = (node: Node, name: string) => node.childForFieldName(name)?.text ?? '';

/** The 0-based last row, not the next row when the node ends with its newline. */
function endRow(node: Node): number {
  const { row, column } = node.endPosition;
  return column === 0 && row > node.startPosition.row ? row - 1 : row;
}

function lastName(node: Node | null, member: string): string | null {
  if (node === null) return null;
  if (node.type === 'identifier' || node.type === 'type_identifier') return node.text;
  if (node.type === member)
    return node.childForFieldName(member === 'attribute' ? 'attribute' : 'property')?.text ?? null;
  return null;
}

// Python ------------------------------------------------------------------------------

const PY_KINDS: Record<string, string> = {
  function_definition: 'function',
  class_definition: 'class',
};

function pythonUnit(node: Node): Found | null {
  const target = node.type === 'decorated_definition' ? node.childForFieldName('definition') : node;
  const kind = target === null ? undefined : PY_KINDS[target.type];
  if (target === null || kind === undefined) return null;
  return {
    kind,
    name: field(target, 'name'),
    body: kind === 'class' ? target.childForFieldName('body') : null,
  };
}

// TypeScript, TSX and JavaScript -------------------------------------------------------

const JS_KINDS: Record<string, string> = {
  function_declaration: 'function',
  generator_function_declaration: 'function',
  class_declaration: 'class',
  abstract_class_declaration: 'class',
  class: 'class',
  method_definition: 'method',
  interface_declaration: 'interface',
  type_alias_declaration: 'type',
  enum_declaration: 'enum',
  internal_module: 'namespace',
};
const FUNCTION_VALUES = new Set([
  'arrow_function',
  'function_expression',
  'function',
  'generator_function',
]);

/** `export …` is the unit of what it exports; an unnamed default export is `default`. */
function exportUnit(node: Node): Found | null {
  for (const inner of node.namedChildren) {
    const found = FUNCTION_VALUES.has(inner.type)
      ? { kind: 'function', name: '', body: null }
      : scriptUnit(inner);
    if (found !== null) return { ...found, name: found.name || 'default' };
  }
  return null;
}

/** `const f = () => …` is a function; any other variable is not a unit. */
function variableUnit(node: Node): Found | null {
  const declarator = node.namedChildren.find(
    (d) =>
      d.type === 'variable_declarator' &&
      FUNCTION_VALUES.has(d.childForFieldName('value')?.type ?? ''),
  );
  if (declarator === undefined) return null;
  return { kind: 'function', name: field(declarator, 'name'), body: null };
}

function scriptUnit(node: Node): Found | null {
  if (node.type === 'export_statement') return exportUnit(node);
  if (node.type === 'lexical_declaration' || node.type === 'variable_declaration') {
    return variableUnit(node);
  }
  const kind = JS_KINDS[node.type];
  if (kind === undefined) return null;
  const nests = kind === 'class' || kind === 'namespace';
  return { kind, name: field(node, 'name'), body: nests ? node.childForFieldName('body') : null };
}

/** A call, a `new` or a JSX element (`<Button />` depends on `Button`). */
function scriptCall(node: Node): string | null {
  switch (node.type) {
    case 'call_expression':
      return lastName(node.childForFieldName('function'), 'member_expression');
    case 'new_expression':
      return lastName(node.childForFieldName('constructor'), 'member_expression');
    case 'jsx_opening_element':
    case 'jsx_self_closing_element': {
      // `<div>` is an HTML element, `<Button>` a component.
      const name = lastName(node.childForFieldName('name'), 'member_expression');
      return name !== null && /^[A-Z]/.test(name) ? name : null;
    }
    default:
      return null;
  }
}

// SQL, YAML, Dockerfile, Markdown -------------------------------------------------------

const SQL_KINDS: Record<string, string> = {
  create_table: 'table',
  create_view: 'view',
  create_materialized_view: 'view',
  create_function: 'function',
  create_index: 'index',
  create_type: 'type',
};

function sqlUnit(node: Node): Found | null {
  if (node.type !== 'statement') return null;
  const inner = node.firstNamedChild;
  const kind = inner === null ? undefined : SQL_KINDS[inner.type];
  if (inner === null || kind === undefined) return { kind: 'statement', name: '', body: null };
  const name = inner.namedChildren.find((c) => c.type === 'object_reference')?.text ?? '';
  return { kind, name, body: null };
}

function yamlUnit(node: Node): Found | null {
  if (node.type !== 'block_mapping_pair') return null;
  return { kind: 'key', name: field(node, 'key'), body: node.childForFieldName('value') };
}

function markdownUnit(node: Node): Found | null {
  if (node.type !== 'section') return null;
  // Text before the first heading is a section without one: it has no name.
  const first = node.firstNamedChild;
  const heading = first?.type.endsWith('_heading') === true ? first : null;
  // The heading's text: `inline` for `# Title`, a `paragraph` for an underlined one.
  const title = heading?.namedChildren.find((c) => !c.type.endsWith('_marker'))?.text ?? '';
  return { kind: 'section', name: title, body: node };
}

/** A stage is a `FROM` and the instructions up to the next one. */
function dockerStages(root: Node): Unit[] {
  const stages: Unit[] = [];
  for (const node of root.namedChildren) {
    if (node.type === 'from_instruction') {
      const alias = node.namedChildren.find((c) => c.type === 'image_alias')?.text ?? '';
      stages.push({
        kind: 'stage',
        name: alias,
        start: node.startPosition.row,
        end: endRow(node),
        children: [],
      });
    } else {
      const last = stages.at(-1);
      if (last !== undefined) stages[stages.length - 1] = { ...last, end: endRow(node) };
    }
  }
  return stages;
}

const SCRIPT: Spec = { prefix: 'ts', unit: scriptUnit, call: scriptCall };

export const SPECS: Record<LanguageId, Spec> = {
  python: {
    prefix: 'py',
    unit: pythonUnit,
    call: (node) =>
      node.type === 'call' ? lastName(node.childForFieldName('function'), 'attribute') : null,
  },
  typescript: SCRIPT,
  tsx: { ...SCRIPT, prefix: 'tsx' },
  javascript: { ...SCRIPT, prefix: 'js' },
  sql: { prefix: 'sql', unit: sqlUnit },
  yaml: { prefix: 'yaml', unit: yamlUnit },
  markdown: { prefix: 'md', unit: markdownUnit },
  dockerfile: { prefix: 'docker', unit: () => null, top: dockerStages },
};

/** The units directly under `root`, looking through nodes that are not units. */
/** Deeper definitions are cut as windows of the unit that holds them. */
export const MAX_UNIT_DEPTH = 32;

/** One push per child: a spread push of a node's children overflows past ~200k. */
function pushChildren(stack: Node[], node: Node): void {
  for (const child of node.namedChildren) stack.push(child);
}

function unitsUnder(spec: Spec, root: Node, depth: number): Unit[] {
  const units: Unit[] = [];
  const stack: Node[] = [];
  pushChildren(stack, root);
  while (stack.length > 0) {
    const node = stack.pop();
    if (node === undefined) break;
    const found = spec.unit(node);
    if (found === null) {
      pushChildren(stack, node);
      continue;
    }
    // Recursion follows the nesting of definitions (classes in classes), capped.
    const body = depth < MAX_UNIT_DEPTH ? found.body : null;
    const children = body === null ? [] : unitsUnder(spec, body, depth + 1);
    units.push({
      kind: found.kind,
      name: found.name,
      start: node.startPosition.row,
      end: endRow(node),
      children,
    });
  }
  return units;
}

export function extractUnits(language: LanguageId, root: Node): Unit[] {
  const spec = SPECS[language];
  return spec.top?.(root) ?? unitsUnder(spec, root, 1);
}

export function extractCalls(language: LanguageId, root: Node): CallSite[] {
  const call = SPECS[language].call;
  if (call === undefined) return [];
  const calls: CallSite[] = [];
  const stack = [root];
  while (stack.length > 0) {
    const node = stack.pop();
    if (node === undefined) break;
    const name = call(node);
    if (name !== null) calls.push({ row: node.startPosition.row, name });
    pushChildren(stack, node);
  }
  return calls;
}

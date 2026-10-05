"""Remove retired inference code from a hash-verified source snapshot.

The transformations operate only on a new release's in-memory files. Historical
releases remain readable, and are never edited or used as inference fallbacks.
"""
import ast


PREFIX = "source/src/korean_pronunciation_poc/"
RETIRED = {"final_pronunciation_detector", "final_pronunciation_artifacts", "xlsr_pronunciation",
           "expected_phone_parity", "pronunciation_acceptance"}


def module(files, name):
    return ast.parse(files[PREFIX + name + ".py"].decode())


def save(files, name, tree):
    files[PREFIX + name + ".py"] = (ast.unparse(ast.fix_missing_locations(tree)) + "\n").encode()


def assigned(node):
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


class PipelinePruner(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        if node.module in RETIRED or node.module == "pronunciation_mvp_policy":
            return None
        if node.module == "pronunciation_reliability":
            node.names = [n for n in node.names if n.name != "build_pronunciation_presentation"]
        return node

    def visit_Assign(self, node):
        if assigned(node) & {"PronunciationCtcRunner", "FinalPronunciationRunner"}:
            return None
        return self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if assigned(node) & {"run_pronunciation_ctc", "run_final_pronunciation"}:
            return None
        if assigned(node) & {"pronunciation_ctc_baseline", "final_pronunciation"}:
            node.annotation, node.value = ast.Constant(None), ast.Constant(None)
        return self.generic_visit(node)

    def visit_If(self, node):
        condition = ast.unparse(node.test)
        if ("self._config.pronunciation.enabled" in condition
                or "self._config.final_pronunciation.enabled" in condition
                or "final_pronunciation.status" in condition):
            return None
        return self.generic_visit(node)


def prune(files):
    config = module(files, "config")
    config.body = [node for node in config.body if not assigned(node) & {"XLSR_STAGE2_MODEL_SHA256"}]
    for node in ast.walk(config):
        if isinstance(node, ast.ClassDef) and node.name in {"XlsrPronunciationSettings", "FinalPronunciationSettings"}:
            node.body = ast.parse('enabled: Literal[False] = False').body
    save(files, "config", config)
    # Pure Hangul symbol tables have no model or checkpoint dependency.
    symbols = module(files, "xlsr_pronunciation")
    symbols.body = [node for node in symbols.body if assigned(node) & {
        "_COMBINING_TO_COMPATIBILITY_JAMO", "_COMPOUND_JAMO_DECOMPOSITIONS"}]
    save(files, "phone_symbols", symbols)
    reliability = module(files, "pronunciation_reliability")
    reliability.body = [node for node in reliability.body if not (
        isinstance(node, ast.FunctionDef) and node.lineno >= 340)]
    reliability.body = [node for node in reliability.body if not (
        isinstance(node, ast.ImportFrom) and node.module == "final_pronunciation_detector")]
    for node in ast.walk(reliability):
        if isinstance(node, ast.ImportFrom) and node.module == "xlsr_pronunciation":
            node.module = "phone_symbols"
        if isinstance(node, ast.FunctionDef) and node.name == "_source_candidates_for_target":
            node.body[1:1] = ast.parse('if (len(source)+1)*(len(target)+1) > 1000000:\n'
                                        '    raise ValueError("NATIVE_PROVENANCE_RESOURCE_LIMIT")').body
    save(files, "pronunciation_reliability", reliability)
    contract = module(files, "pronunciation_phone_contract")
    for node in ast.walk(contract):
        if isinstance(node, ast.ImportFrom) and node.module == "xlsr_pronunciation":
            node.module = "phone_symbols"
    save(files, "pronunciation_phone_contract", contract)
    pipeline = PipelinePruner().visit(module(files, "pipeline"))
    for node in ast.walk(pipeline):
        if isinstance(node, ast.ImportFrom) and node.module == "config":
            node.names = [n for n in node.names if n.name not in {"XlsrPronunciationSettings", "FinalPronunciationSettings"}]
    for node in ast.walk(pipeline):
        if isinstance(node, ast.ClassDef) and node.name == "AnalysisPipeline":
            constructor = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
            constructor.body[:0] = ast.parse('if config.pronunciation.enabled or config.final_pronunciation.enabled:\n'
                                            '    raise ValueError("RETIRED_PRONUNCIATION_EXECUTION_FORBIDDEN")').body
    save(files, "pipeline", pipeline)
    core = module(files, "ai_core")
    core.body = [node for node in core.body if not (
        isinstance(node, ast.ImportFrom) and node.module == "final_pronunciation_artifacts")]
    for node in ast.walk(core):
        if isinstance(node, ast.AnnAssign):
            if assigned(node) == {"detector_version"}:
                node.value = ast.Constant("RETIRED_NOT_EXECUTED")
            elif assigned(node) == {"stage1_model_safetensors_sha256"}:
                node.annotation, node.value = ast.Constant(None), ast.Constant(None)
        if isinstance(node, ast.FunctionDef) and node.name == "_pronunciation_result":
            node.body = ast.parse('return AICorePronunciationResult(status="not_requested")').body
        if isinstance(node, ast.keyword) and node.arg == "pronunciation_requested":
            node.value = ast.Constant(False)
    save(files, "ai_core", core)
    policy = module(files, "ai_core_policy")
    policy.body = [node for node in policy.body if not (
        isinstance(node, ast.ImportFrom) and node.module == "final_pronunciation_detector")]
    for node in ast.walk(policy):
        if isinstance(node, ast.arg) and node.arg == "pronunciation":
            node.annotation = ast.Constant(None)
        if isinstance(node, ast.FunctionDef) and node.name == "build_ai_core_evidence_policy":
            node.body = [part for part in node.body if not (
                isinstance(part, ast.If) and ast.unparse(part.test) == "pronunciation_requested")]
    save(files, "ai_core_policy", policy)
    removed = []
    for path in list(files):
        if path.startswith(PREFIX) and path[len(PREFIX):].removesuffix(".py") in RETIRED:
            removed.append(path)
            del files[path]
    if len(removed) != len(RETIRED):
        raise ValueError("NATIVE_PRUNING_SOURCE_CHANGED")
    for name, raw in files.items():
        if name.endswith(".py"):
            tree = ast.parse(raw)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] in RETIRED:
                    raise ValueError("NATIVE_RETIRED_IMPORT_REMAINS")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
                    "run_final_pronunciation_inference", "get_final_pronunciation_runtime", "run_pronunciation_ctc_baseline"}:
                    raise ValueError("NATIVE_RETIRED_CALL_REMAINS")
    return removed

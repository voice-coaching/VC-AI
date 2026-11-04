"""Public allowlist: no private CORE, model diagnostics, hashes or storage paths."""


def public_view(result: dict) -> dict:
    assessment = result["assessment"]
    return dict(schemaVersion="voice-coaching.six-dimension-view.v1",
                status=assessment["status"], validationStatus=assessment["validationStatus"],
                policyRevision=assessment["policyRevision"], generationStatus=result["generationStatus"],
                criteria=[{key: criterion[key] for key in
                           ("criterionId", "label", "scope", "status", "score", "components")}
                          for criterion in assessment["criteria"]],
                selectedCriterionIds=assessment["selectedCriterionIds"],
                feedback=[{key: item[key] for key in ("criterionId", "observation", "practice")}
                          for item in result["feedback"]])

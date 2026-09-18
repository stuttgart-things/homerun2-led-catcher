"""The KCL profile does not emit a Namespace (#39, #40).

Every co-tenanted homerun2 component renders from its own KCL profile into its
own kustomize OCI. When those all shipped a `Namespace`, several Argo CD
Applications claimed ownership of the same one:

    SharedResourceWarning: Namespace/homerun2 is part of applications
      argocd/homerun2-...-omni-pitcher and argocd/homerun2-...-core-catcher

One Application's prune deleted it, the rest of the stack went with it,
selfHeal recreated everything, and the cycle repeated — observed live on
homerun2-dev. The consuming Application's `syncOptions: CreateNamespace=true`
creates the namespace without putting any child in charge of pruning it, and
PR-preview namespaces are pre-created by Kyverno.

`kcl/namespace.k` was removed in cbf9c14. This is the guard: the KCL toolchain
is not a test dependency, so the check is structural — it reads the sources
rather than rendering them, which is enough to catch the resource coming back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

KCL_DIR = Path(__file__).resolve().parent.parent / "kcl"
MAIN = KCL_DIR / "main.k"

# Everything the profile is supposed to ship. Argo CD creates the namespace
# itself; nothing else here is namespace-scoped ownership anyone shares.
EXPECTED_MANIFESTS = {
    "serviceaccount.serviceAccount",
    "configmap.configMap",
    "profile_configmap.profileConfigMap",
    "secret.secretRedis",
    "secret.secretApi",
    "deploy.deployment",
    "service.service",
    "httproute.httproute",
}

_MANIFEST_LIST = re.compile(r"manifests\s*=\s*\[\s*(.*?)\]\s*if m\s*\]", re.DOTALL)
_KIND = re.compile(r"""kind\s*[:=]\s*["']([A-Za-z]+)["']""")


def exported_manifests() -> set[str]:
    """The resource references inside main.k's `manifests` comprehension."""
    match = _MANIFEST_LIST.search(MAIN.read_text())
    assert match, "could not find the manifests list in kcl/main.k"

    entries = set()
    for line in match.group(1).splitlines():
        line = line.split("#", 1)[0].strip().rstrip(",")
        if line and line != "m for m in [":
            entries.add(line)
    return entries


def test_no_namespace_module_exists():
    assert not (KCL_DIR / "namespace.k").exists(), (
        "kcl/namespace.k is back — a Namespace in this profile makes every "
        "co-tenanted Application claim ownership of it (#39)"
    )


@pytest.mark.parametrize("source", sorted(KCL_DIR.glob("*.k")), ids=lambda p: p.name)
def test_no_kcl_module_declares_a_namespace_resource(source):
    kinds = set(_KIND.findall(source.read_text()))

    assert "Namespace" not in kinds, f"{source.name} emits a Namespace resource (#39)"


def test_main_exports_exactly_the_expected_resources():
    exported = exported_manifests()

    # Not just "no namespace": a main.k that exported nothing would also pass
    # that, and would be a far worse bug.
    assert exported == EXPECTED_MANIFESTS, (
        "kcl/main.k exports a different set of resources than expected — "
        f"added {sorted(exported - EXPECTED_MANIFESTS)}, "
        f"removed {sorted(EXPECTED_MANIFESTS - exported)}"
    )


def test_main_says_why_the_namespace_is_absent():
    """The absence is deliberate, and the next person needs to know that."""
    text = MAIN.read_text()

    assert "Namespace is NOT emitted" in text
    assert "CreateNamespace=true" in text

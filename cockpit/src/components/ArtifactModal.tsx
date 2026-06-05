import { Modal } from "./Modal";
import type { Artifact } from "../api";
import { Button } from "./ui/Button";
import { OrbitGraph } from "./OrbitGraph";

// Read-only view of a repo artifact (FUNC/REQ/INT/ADR) + its provenance orbit.
export function ArtifactModal({ artifact, onClose, onOpen }: {
  artifact: Artifact; onClose: () => void; onOpen?: (id: string) => void;
}) {
  const lineage = artifact.lineage || [];
  return (
    <Modal eyebrow={`${artifact.type} · ${artifact.id}`} title={artifact.title} onClose={onClose}
      footer={<><span className="grow1" /><Button variant="ghost" onClick={onClose}>Close</Button></>}>
      {lineage.length > 0 && (
        <div className="artifact-orbit">
          <div className="review-label">Provenance — what fed this artifact</div>
          <OrbitGraph center={{ label: artifact.id, kind: artifact.type }}
                      sources={lineage.map((id) => ({ id }))} onOpen={onOpen} />
        </div>
      )}
      <pre className="artifact-text">{artifact.text}</pre>
    </Modal>
  );
}

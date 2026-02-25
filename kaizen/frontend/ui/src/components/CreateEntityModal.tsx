import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';

interface PolicyTrigger {
    type: string;
    value: string | string[];
    target: string;
    operator: string;
    threshold: number;
}

interface CreateEntityModalProps {
    namespaceId: string;
    onClose: () => void;
    onCreated: () => void;
}

export default function CreateEntityModal({ namespaceId, onClose, onCreated }: CreateEntityModalProps) {
    const [newTypeOption, setNewTypeOption] = useState('guideline');
    const [customType, setCustomType] = useState('');
    const [newContent, setNewContent] = useState('');
    const [newMetadata, setNewMetadata] = useState('');
    const [createError, setCreateError] = useState<string | null>(null);

    const [guideRationale, setGuideRationale] = useState('');
    const [guideCategory, setGuideCategory] = useState('strategy');
    const [guideTrigger, setGuideTrigger] = useState('');

    const [policyName, setPolicyName] = useState('');
    const [policyDesc, setPolicyDesc] = useState('');
    const [policyTypeEnum, setPolicyTypeEnum] = useState('playbook');
    const [policyPriority, setPolicyPriority] = useState(50);
    const [policyEnabled, setPolicyEnabled] = useState(true);

    const [policyTriggers, setPolicyTriggers] = useState<PolicyTrigger[]>([
        { type: 'keyword', value: '', target: 'intent', operator: 'or', threshold: 0.7 }
    ]);

    const addTrigger = () => {
        setPolicyTriggers([...policyTriggers, { type: 'keyword', value: '', target: 'intent', operator: 'or', threshold: 0.7 }]);
    };

    const removeTrigger = (index: number) => {
        setPolicyTriggers(policyTriggers.filter((_, i) => i !== index));
    };

    const updateTrigger = (index: number, field: string, val: any) => {
        const newTriggers = [...policyTriggers];
        newTriggers[index] = { ...newTriggers[index], [field]: val };
        setPolicyTriggers(newTriggers);
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setCreateError(null);

        const activeType = newTypeOption === 'other' ? customType.trim() : newTypeOption;

        if (!activeType) {
            setCreateError("Entity type cannot be empty.");
            return;
        }

        if (!newContent.trim()) {
            setCreateError("Entity content cannot be empty.");
            return;
        }

        let parsedMetadata = {};

        if (activeType === "guideline") {
            if (!guideRationale.trim() || !guideTrigger.trim()) {
                setCreateError("Guidelines require a rationale and trigger.");
                return;
            }
            parsedMetadata = {
                rationale: guideRationale,
                category: guideCategory,
                trigger: guideTrigger
            };
        } else if (activeType === "policy") {
            if (!policyName.trim() || !policyDesc.trim() || policyTriggers.length === 0) {
                setCreateError("Policies require a name, description, and at least one trigger.");
                return;
            }
            parsedMetadata = {
                name: policyName,
                description: policyDesc,
                policy_type: policyTypeEnum,
                priority: policyPriority,
                enabled: policyEnabled,
                triggers: policyTriggers
            };
        } else {
            if (newMetadata.trim()) {
                try {
                    parsedMetadata = JSON.parse(newMetadata);
                } catch {
                    setCreateError("Metadata must be valid JSON.");
                    return;
                }
            }
        }

        try {
            const res = await fetch(`/api/namespaces/${encodeURIComponent(namespaceId)}/entities`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    type: activeType,
                    content: newContent.trim(),
                    metadata: parsedMetadata
                })
            });

            if (!res.ok) {
                let errorMsg = `HTTP ${res.status} ${res.statusText}`;
                try {
                    const d = await res.json();
                    errorMsg = d.detail || errorMsg;
                } catch {
                    const text = await res.text();
                    if (text) errorMsg += `: ${text}`;
                }
                throw new Error(errorMsg);
            }

            onCreated();
        } catch (err: any) {
            setCreateError(err.message);
        }
    };

    return (
        <div className="modal-backdrop">
            <div className="glass-panel modal">
                <h3 className="modal-title">Create New Entity</h3>

                {createError && (
                    <div className="error-inline">{createError}</div>
                )}

                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label>Type</label>
                        <div className="form-row">
                            <select
                                className="form-control flex-1"
                                value={newTypeOption}
                                onChange={(e) => setNewTypeOption(e.target.value)}
                            >
                                <option value="guideline">Guideline</option>
                                <option value="policy">Policy</option>
                                <option value="other">Other...</option>
                            </select>
                            {newTypeOption === 'other' && (
                                <input
                                    type="text"
                                    className="form-control flex-1"
                                    value={customType}
                                    onChange={(e) => setCustomType(e.target.value)}
                                    placeholder="Enter custom type"
                                    required
                                />
                            )}
                        </div>
                    </div>

                    <div className="form-group">
                        <label>Content</label>
                        <textarea
                            className="form-control"
                            value={newContent}
                            onChange={(e) => setNewContent(e.target.value)}
                            placeholder="Enter the entity content or payload..."
                            rows={newTypeOption === 'policy' ? 3 : 5}
                            required
                        />
                    </div>

                    {newTypeOption === 'guideline' ? (
                        <>
                            <div className="form-group">
                                <label>Rationale</label>
                                <textarea
                                    className="form-control"
                                    value={guideRationale}
                                    onChange={(e) => setGuideRationale(e.target.value)}
                                    placeholder="Why this tip helps..."
                                    rows={2}
                                    required
                                />
                            </div>
                            <div className="form-group form-row">
                                <div className="flex-1">
                                    <label>Category</label>
                                    <select
                                        className="form-control"
                                        value={guideCategory}
                                        onChange={(e) => setGuideCategory(e.target.value)}
                                    >
                                        <option value="strategy">Strategy</option>
                                        <option value="recovery">Recovery</option>
                                        <option value="optimization">Optimization</option>
                                    </select>
                                </div>
                                <div className="flex-2">
                                    <label>Trigger</label>
                                    <input
                                        type="text"
                                        className="form-control"
                                        value={guideTrigger}
                                        onChange={(e) => setGuideTrigger(e.target.value)}
                                        placeholder="When to apply this tip..."
                                        required
                                    />
                                </div>
                            </div>
                        </>
                    ) : newTypeOption === 'policy' ? (
                        <>
                            <div className="form-group">
                                <label>Policy Name</label>
                                <input
                                    type="text"
                                    className="form-control"
                                    value={policyName}
                                    onChange={(e) => setPolicyName(e.target.value)}
                                    placeholder="e.g. Checkout Rules"
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label>Description</label>
                                <textarea
                                    className="form-control"
                                    value={policyDesc}
                                    onChange={(e) => setPolicyDesc(e.target.value)}
                                    placeholder="What does this policy govern..."
                                    rows={2}
                                    required
                                />
                            </div>
                            <div className="form-group form-row">
                                <div className="flex-2">
                                    <label>Policy Type</label>
                                    <select
                                        className="form-control"
                                        value={policyTypeEnum}
                                        onChange={(e) => setPolicyTypeEnum(e.target.value)}
                                    >
                                        <option value="playbook">Playbook</option>
                                        <option value="intent_guard">Intent Guard</option>
                                        <option value="tool_guide">Tool Guide</option>
                                        <option value="tool_approval">Tool Approval</option>
                                        <option value="output_formatter">Output Formatter</option>
                                    </select>
                                </div>
                                <div className="flex-1">
                                    <label>Priority</label>
                                    <input
                                        type="number"
                                        className="form-control"
                                        value={policyPriority}
                                        onChange={(e) => {
                                            const v = parseInt(e.target.value, 10);
                                            setPolicyPriority(Number.isNaN(v) ? 50 : v);
                                        }}
                                    />
                                </div>
                            </div>
                            <div className="form-group">
                                <div className="d-flex justify-end align-center" style={{ marginBottom: '0.5rem' }}>
                                    <label className="no-margin">Triggers</label>
                                    <button
                                        type="button"
                                        onClick={addTrigger}
                                        className="btn btn-add-trigger"
                                        style={{ marginLeft: 'auto' }}
                                    >
                                        <Plus size={14} /> Add Trigger
                                    </button>
                                </div>

                                {policyTriggers.map((t, idx) => (
                                    <div key={idx} className="trigger-card">
                                        {policyTriggers.length > 1 && (
                                            <button
                                                type="button"
                                                onClick={() => removeTrigger(idx)}
                                                className="trigger-remove-btn"
                                            >
                                                <Trash2 size={16} />
                                            </button>
                                        )}

                                        <div className="trigger-grid">
                                            <div>
                                                <label className="trigger-label">Type</label>
                                                <select
                                                    className="form-control trigger-compact"
                                                    value={t.type}
                                                    onChange={(e) => updateTrigger(idx, 'type', e.target.value)}
                                                >
                                                    <option value="keyword">Keyword</option>
                                                    <option value="regex">Regex</option>
                                                    <option value="intent">Intent</option>
                                                </select>
                                            </div>
                                            <div>
                                                <label className="trigger-label">Target</label>
                                                <select
                                                    className="form-control trigger-compact"
                                                    value={t.target}
                                                    onChange={(e) => updateTrigger(idx, 'target', e.target.value)}
                                                >
                                                    <option value="intent">Intent</option>
                                                    <option value="input">Input</option>
                                                    <option value="output">Output</option>
                                                    <option value="tool_call">Tool Call</option>
                                                </select>
                                            </div>
                                        </div>

                                        <div className="d-flex gap-2">
                                            <div className="flex-1">
                                                <label className="trigger-label">Values (comma-separated if multiple)</label>
                                                <input
                                                    type="text"
                                                    className="form-control trigger-compact"
                                                    value={Array.isArray(t.value) ? t.value.join(', ') : t.value}
                                                    onChange={(e) => {
                                                        const val = e.target.value;
                                                        const arr = val.includes(',') ? val.split(',').map(s => s.trim()) : (val ? [val] : []);
                                                        updateTrigger(idx, 'value', val.includes(',') ? arr : val);
                                                    }}
                                                    placeholder="e.g. drop table, delete"
                                                    required
                                                />
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                            <div className="form-group--inline">
                                <input
                                    type="checkbox"
                                    id="policyStatus"
                                    checked={policyEnabled}
                                    onChange={(e) => setPolicyEnabled(e.target.checked)}
                                />
                                <label htmlFor="policyStatus" className="no-margin">Policy Enabled active state</label>
                            </div>
                        </>
                    ) : (
                        <div className="form-group">
                            <label>Metadata (Optional JSON)</label>
                            <textarea
                                className="form-control"
                                value={newMetadata}
                                onChange={(e) => setNewMetadata(e.target.value)}
                                placeholder='{"key": "value"}'
                                rows={3}
                            />
                            <small className="text-secondary form-hint">Must be valid JSON if provided.</small>
                        </div>
                    )}

                    <div className="modal-actions">
                        <button type="button" className="btn btn-secondary" onClick={onClose}>
                            Cancel
                        </button>
                        <button type="submit" className="btn btn-primary">
                            Create
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}

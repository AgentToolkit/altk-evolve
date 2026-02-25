import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Search, AlertCircle, RefreshCw, Layers, Eye, Trash2, Plus } from 'lucide-react';

interface Entity {
    id: string;
    type: string;
    content: string;
    created_at?: string;
    metadata: Record<string, any>;
}

export default function EntityExplorer() {
    const { id } = useParams<{ id: string }>();
    const [entities, setEntities] = useState<Entity[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [filterType, setFilterType] = useState<string>("");
    const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);

    // Create Modal State
    const [isCreateOpen, setIsCreateOpen] = useState(false);
    const [newTypeOption, setNewTypeOption] = useState('guideline');
    const [customType, setCustomType] = useState('');
    const [newContent, setNewContent] = useState('');
    const [newMetadata, setNewMetadata] = useState('');
    const [createError, setCreateError] = useState<string | null>(null);

    // Guideline Specific State
    const [guideRationale, setGuideRationale] = useState('');
    const [guideCategory, setGuideCategory] = useState('strategy');
    const [guideTrigger, setGuideTrigger] = useState('');

    // Policy Specific State
    const [policyName, setPolicyName] = useState('');
    const [policyDesc, setPolicyDesc] = useState('');
    const [policyTypeEnum, setPolicyTypeEnum] = useState('playbook');
    const [policyPriority, setPolicyPriority] = useState(50);
    const [policyEnabled, setPolicyEnabled] = useState(true);

    // Policy Trigger Builder State
    const [policyTriggers, setPolicyTriggers] = useState([{ type: 'keyword', value: '', target: 'intent', operator: 'or', threshold: 0.7 }]);

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

    const resetForm = () => {
        setNewTypeOption("guideline");
        setCustomType("");
        setNewContent("");
        setNewMetadata("");

        setGuideRationale("");
        setGuideCategory("strategy");
        setGuideTrigger("");

        setPolicyName("");
        setPolicyDesc("");
        setPolicyTypeEnum("playbook");
        setPolicyPriority(50);
        setPolicyEnabled(true);
        setPolicyTriggers([{ type: 'keyword', value: '', target: 'intent', operator: 'or', threshold: 0.7 }]);

        setCreateError(null);
    };

    const fetchEntities = () => {
        setLoading(true);
        let url = `/api/namespaces/${encodeURIComponent(id || '')}/entities`;
        if (filterType) {
            url += `?type=${encodeURIComponent(filterType)}`;
        }

        fetch(url)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch entities');
                return res.json();
            })
            .then(data => {
                setEntities(data);
                setError(null);
            })
            .catch(err => setError(err.message))
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        if (id) {
            fetchEntities();
        }
    }, [id, filterType]);

    const handleDelete = async (entityId: string) => {
        if (!confirm('Are you sure you want to delete this entity?')) return;

        try {
            const res = await fetch(`/api/namespaces/${encodeURIComponent(id || '')}/entities/${encodeURIComponent(entityId)}`, {
                method: 'DELETE'
            });
            if (!res.ok) {
                const d = await res.json();
                throw new Error(d.detail || 'Failed to delete entity');
            }
            // Refresh list
            fetchEntities();
            if (selectedEntity?.id === entityId) {
                setSelectedEntity(null);
            }
        } catch (err: any) {
            alert('Error deleting entity: ' + err.message);
        }
    };

    const handleCreate = async (e: React.FormEvent) => {
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
            try {
                parsedMetadata = {
                    name: policyName,
                    description: policyDesc,
                    policy_type: policyTypeEnum,
                    priority: policyPriority,
                    enabled: policyEnabled,
                    triggers: policyTriggers
                };
            } catch (err) {
                setCreateError("Failed to build policy metadata payload.");
                return;
            }
        } else {
            if (newMetadata.trim()) {
                try {
                    parsedMetadata = JSON.parse(newMetadata);
                } catch (err) {
                    setCreateError("Metadata must be valid JSON.");
                    return;
                }
            }
        }

        try {
            const res = await fetch(`/api/namespaces/${encodeURIComponent(id || '')}/entities`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    type: activeType,
                    content: newContent.trim(),
                    metadata: parsedMetadata
                })
            });

            if (!res.ok) {
                const d = await res.json();
                throw new Error(d.detail || 'Failed to create entity');
            }

            // Reset form and refresh
            resetForm();
            setIsCreateOpen(false);
            fetchEntities();
        } catch (err: any) {
            setCreateError(err.message);
        }
    };

    return (
        <div className="entity-explorer-container">
            <div className="page-header" style={{ marginBottom: "1.5rem" }}>
                <div>
                    <h2 className="section-title d-flex align-center gap-2" style={{ border: 'none', padding: 0, margin: 0 }}>
                        <Link to="/namespaces" className="btn-icon">
                            <ArrowLeft size={20} />
                        </Link>
                        <Layers size={22} className="text-primary" />
                        Namespace: {id}
                    </h2>
                    <p className="text-secondary" style={{ marginLeft: "2.5rem", marginTop: "0.25rem" }}>
                        Browse and filter entities within this namespace
                    </p>
                </div>
            </div>

            <div className="glass-panel" style={{ padding: "1rem", marginBottom: "1.5rem", display: "flex", gap: "1rem" }}>
                <div className="form-group" style={{ margin: 0, flex: 1 }}>
                    <div className="d-flex align-center gap-2" style={{ background: "rgba(15, 23, 42, 0.6)", borderRadius: "6px", padding: "0.5rem 1rem", border: "1px solid var(--border-light)" }}>
                        <Search size={18} className="text-secondary" />
                        <input
                            type="text"
                            placeholder="Filter by type (e.g. guideline)"
                            value={filterType}
                            onChange={(e) => setFilterType(e.target.value)}
                            style={{ background: "transparent", border: "none", color: "white", outline: "none", flex: 1 }}
                        />
                    </div>
                </div>
                <button className="btn btn-primary" onClick={() => setIsCreateOpen(true)}>
                    <Plus size={18} /> Create Entity
                </button>
                <button className="btn btn-secondary" onClick={fetchEntities}>
                    <RefreshCw size={18} /> Refresh
                </button>
            </div>

            {error ? (
                <div className="error-state">
                    <AlertCircle size={40} />
                    <p>{error}</p>
                    <button className="btn btn-secondary" onClick={fetchEntities}>
                        <RefreshCw size={16} /> Retry
                    </button>
                </div>
            ) : loading ? (
                <div className="loading-state">
                    <div className="loader" data-testid="loader"></div>
                </div>
            ) : (
                <div className="glass-panel table-container">
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th style={{ width: '150px' }}>Type</th>
                                <th>Content</th>
                                <th>Created</th>
                                <th className="text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {entities.length === 0 ? (
                                <tr>
                                    <td colSpan={4} className="text-center py-8 text-secondary">
                                        No entities found in this namespace.
                                    </td>
                                </tr>
                            ) : (
                                entities.map(ent => (
                                    <tr key={ent.id}>
                                        <td>
                                            <span className="badge">{ent.type}</span>
                                        </td>
                                        <td>
                                            <div style={{ fontSize: "0.95rem", color: "#CBD5E1" }}>
                                                {ent.content.length > 100 ? `${ent.content.substring(0, 100)}...` : ent.content}
                                            </div>
                                        </td>
                                        <td className="text-secondary small-text">
                                            {ent.created_at ? new Date(ent.created_at).toLocaleString() : 'N/A'}
                                        </td>
                                        <td className="text-right d-flex justify-end gap-2" style={{ border: 'none' }}>
                                            <button
                                                className="btn-icon"
                                                title="View Details"
                                                onClick={() => setSelectedEntity(ent)}
                                            >
                                                <Eye size={18} />
                                            </button>
                                            <button
                                                className="btn-icon text-danger"
                                                title="Delete Entity"
                                                onClick={() => handleDelete(ent.id)}
                                            >
                                                <Trash2 size={18} />
                                            </button>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>
            )}

            {selectedEntity && (
                <div className="modal-backdrop" onClick={() => setSelectedEntity(null)}>
                    <div className="glass-panel modal" style={{ maxWidth: "800px", maxHeight: "90vh", overflowY: "auto" }} onClick={e => e.stopPropagation()}>
                        <div className="d-flex align-center justify-between" style={{ marginBottom: "1.5rem" }}>
                            <h3 className="modal-title" style={{ margin: 0 }}>Entity Details</h3>
                            <span className="entity-type">{selectedEntity.type}</span>
                        </div>

                        <div style={{ marginBottom: "1.5rem" }}>
                            <label style={{ display: "block", color: "var(--text-secondary)", marginBottom: "0.5rem", fontSize: "0.875rem" }}>ID</label>
                            <div style={{ background: "rgba(15, 23, 42, 0.4)", padding: "0.75rem", borderRadius: "6px", fontFamily: "monospace", fontSize: "0.9rem" }}>
                                {selectedEntity.id}
                            </div>
                        </div>

                        <div style={{ marginBottom: "1.5rem" }}>
                            <label style={{ display: "block", color: "var(--text-secondary)", marginBottom: "0.5rem", fontSize: "0.875rem" }}>Content</label>
                            <div style={{ background: "rgba(15, 23, 42, 0.4)", padding: "1rem", borderRadius: "6px", lineHeight: "1.6", whiteSpace: "pre-wrap" }}>
                                {selectedEntity.content}
                            </div>
                        </div>

                        {Object.keys(selectedEntity.metadata).length > 0 && (
                            <div style={{ marginBottom: "1.5rem" }}>
                                <label style={{ display: "block", color: "var(--text-secondary)", marginBottom: "0.5rem", fontSize: "0.875rem" }}>Metadata</label>
                                <div style={{ background: "rgba(15, 23, 42, 0.4)", padding: "1rem", borderRadius: "6px", fontFamily: "monospace", fontSize: "0.9rem", whiteSpace: "pre-wrap", overflowX: "auto" }}>
                                    {JSON.stringify(selectedEntity.metadata, null, 2)}
                                </div>
                            </div>
                        )}

                        <div className="modal-actions" style={{ marginTop: "2rem" }}>
                            <button type="button" className="btn btn-secondary" onClick={() => setSelectedEntity(null)}>Close</button>
                        </div>
                    </div>
                </div>
            )}
            {isCreateOpen && (
                <div className="modal-backdrop">
                    <div className="glass-panel modal">
                        <h3 className="modal-title">Create New Entity</h3>

                        {createError && (
                            <div className="error-message" style={{ marginBottom: "1rem", padding: "0.75rem", background: "rgba(239, 68, 68, 0.1)", color: "#ef4444", borderRadius: "6px", border: "1px solid rgba(239, 68, 68, 0.2)" }}>
                                {createError}
                            </div>
                        )}

                        <form onSubmit={handleCreate}>
                            <div className="form-group">
                                <label>Type</label>
                                <div style={{ display: 'flex', gap: '1rem' }}>
                                    <select
                                        className="form-control"
                                        value={newTypeOption}
                                        onChange={(e) => setNewTypeOption(e.target.value)}
                                        style={{ flex: 1 }}
                                    >
                                        <option value="guideline">Guideline</option>
                                        <option value="policy">Policy</option>
                                        <option value="other">Other...</option>
                                    </select>
                                    {newTypeOption === 'other' && (
                                        <input
                                            type="text"
                                            className="form-control"
                                            value={customType}
                                            onChange={(e) => setCustomType(e.target.value)}
                                            placeholder="Enter custom type"
                                            style={{ flex: 1 }}
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
                                    <div className="form-group" style={{ display: 'flex', gap: '1rem' }}>
                                        <div style={{ flex: 1 }}>
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
                                        <div style={{ flex: 2 }}>
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
                                    <div className="form-group" style={{ display: 'flex', gap: '1rem' }}>
                                        <div style={{ flex: 2 }}>
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
                                        <div style={{ flex: 1 }}>
                                            <label>Priority</label>
                                            <input
                                                type="number"
                                                className="form-control"
                                                value={policyPriority}
                                                onChange={(e) => setPolicyPriority(parseInt(e.target.value) || 50)}
                                            />
                                        </div>
                                    </div>
                                    <div className="form-group" style={{ marginBottom: '1.5rem' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                                            <label style={{ margin: 0 }}>Triggers</label>
                                            <button
                                                type="button"
                                                onClick={addTrigger}
                                                className="btn"
                                                style={{ padding: '0.25rem 0.5rem', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '0.25rem', background: 'rgba(56, 189, 248, 0.1)', color: '#38bdf8', border: '1px solid rgba(56, 189, 248, 0.2)' }}
                                            >
                                                <Plus size={14} /> Add Trigger
                                            </button>
                                        </div>

                                        {policyTriggers.map((t, idx) => (
                                            <div key={idx} style={{ padding: '1rem', background: 'rgba(15, 23, 42, 0.3)', border: '1px solid rgba(255, 255, 255, 0.1)', borderRadius: '6px', marginBottom: '0.75rem', position: 'relative' }}>
                                                {policyTriggers.length > 1 && (
                                                    <button
                                                        type="button"
                                                        onClick={() => removeTrigger(idx)}
                                                        style={{ position: 'absolute', top: '0.5rem', right: '0.5rem', background: 'none', border: 'none', color: '#ef4444', cursor: 'pointer', padding: '0.25rem' }}
                                                    >
                                                        <Trash2 size={16} />
                                                    </button>
                                                )}

                                                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '0.75rem', marginBottom: '0.75rem' }}>
                                                    <div>
                                                        <label style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Type</label>
                                                        <select
                                                            className="form-control"
                                                            value={t.type}
                                                            onChange={(e) => updateTrigger(idx, 'type', e.target.value)}
                                                            style={{ padding: '0.4rem', fontSize: '0.9rem' }}
                                                        >
                                                            <option value="keyword">Keyword</option>
                                                            <option value="regex">Regex</option>
                                                            <option value="intent">Intent</option>
                                                        </select>
                                                    </div>
                                                    <div>
                                                        <label style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Target</label>
                                                        <select
                                                            className="form-control"
                                                            value={t.target}
                                                            onChange={(e) => updateTrigger(idx, 'target', e.target.value)}
                                                            style={{ padding: '0.4rem', fontSize: '0.9rem' }}
                                                        >
                                                            <option value="intent">Intent</option>
                                                            <option value="input">Input</option>
                                                            <option value="output">Output</option>
                                                            <option value="tool_call">Tool Call</option>
                                                        </select>
                                                    </div>
                                                </div>

                                                <div style={{ display: 'flex', gap: '0.75rem' }}>
                                                    <div style={{ flex: 1 }}>
                                                        <label style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Values (comma-separated if multiple)</label>
                                                        <input
                                                            type="text"
                                                            className="form-control"
                                                            value={Array.isArray(t.value) ? t.value.join(', ') : t.value}
                                                            onChange={(e) => {
                                                                // Convert comma separated lists back into string array automatically behind UI layer
                                                                const val = e.target.value;
                                                                const arr = val.includes(',') ? val.split(',').map(s => s.trim()) : (val ? [val] : []);
                                                                updateTrigger(idx, 'value', val.includes(',') ? arr : val);
                                                            }}
                                                            placeholder="e.g. drop table, delete"
                                                            style={{ padding: '0.4rem', fontSize: '0.9rem' }}
                                                            required
                                                        />
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                    <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
                                        <input
                                            type="checkbox"
                                            id="policyStatus"
                                            checked={policyEnabled}
                                            onChange={(e) => setPolicyEnabled(e.target.checked)}
                                        />
                                        <label htmlFor="policyStatus" style={{ margin: 0 }}>Policy Enabled active state</label>
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
                                    <small className="text-secondary" style={{ display: "block", marginTop: "0.25rem" }}>Must be valid JSON if provided.</small>
                                </div>
                            )}

                            <div className="modal-actions">
                                <button
                                    type="button"
                                    className="btn btn-secondary"
                                    onClick={() => {
                                        setIsCreateOpen(false);
                                        setCreateError(null);
                                    }}
                                >
                                    Cancel
                                </button>
                                <button type="submit" className="btn btn-primary">
                                    Create
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    );
}

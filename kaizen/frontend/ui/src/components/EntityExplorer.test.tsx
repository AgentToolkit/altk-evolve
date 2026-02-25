import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import EntityExplorer from './EntityExplorer';

// Mock the global fetch
globalThis.fetch = vi.fn();

// Add missing global confirm for Node environment
globalThis.confirm = vi.fn(() => true) as any;

describe('EntityExplorer Component', () => {
    beforeEach(() => {
        vi.resetAllMocks();
    });

    const renderWithRouter = () => {
        return render(
            <MemoryRouter initialEntries={['/namespaces/test-ns/entities']}>
                <Routes>
                    <Route path="/namespaces/:id/entities" element={<EntityExplorer />} />
                </Routes>
            </MemoryRouter>
        );
    };

    it('renders loader initially', () => {
        (globalThis.fetch as any).mockImplementation(() => new Promise(() => { }));
        renderWithRouter();
        expect(screen.getByTestId('loader')).toBeInTheDocument();
    });

    it('renders entities table data correctly', async () => {
        const mockEntities = [
            { id: '1', type: 'guideline', content: 'Test guideline content', metadata: {} },
            { id: '2', type: 'task', content: 'Test task content', metadata: { foo: 'bar' } },
        ];

        (globalThis.fetch as any).mockResolvedValueOnce({
            ok: true,
            json: async () => mockEntities,
        });

        renderWithRouter();

        await waitFor(() => {
            expect(screen.getByText('guideline')).toBeInTheDocument();
        });

        expect(screen.getByText('Test guideline content')).toBeInTheDocument();
        expect(screen.getByText('task')).toBeInTheDocument();
        expect(screen.getByText('Test task content')).toBeInTheDocument();
    });

    it('opens create modal when clicking Create Entity', async () => {
        (globalThis.fetch as any).mockResolvedValueOnce({
            ok: true,
            json: async () => [],
        });

        renderWithRouter();

        await waitFor(() => {
            expect(screen.getByText('Create Entity')).toBeInTheDocument();
        });

        const user = userEvent.setup();
        await user.click(screen.getByText('Create Entity'));

        expect(screen.getByText('Create New Entity')).toBeInTheDocument();
    });
});

import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import Namespaces from './Namespaces';

// Mock the global fetch
globalThis.fetch = vi.fn();

describe('Namespaces Component', () => {
    beforeEach(() => {
        vi.resetAllMocks();
    });

    it('renders loader initially', () => {
        // Return a promise that doesn't resolve immediately to keep loading state
        (globalThis.fetch as any).mockImplementation(() => new Promise(() => { }));

        render(
            <BrowserRouter>
                <Namespaces />
            </BrowserRouter>
        );

        expect(screen.getByTestId('loader')).toBeInTheDocument();
    });

    it('renders namespaces table data correctly', async () => {
        const mockNamespaces = [
            { id: 'test-namespace-1', amount_of_entities: 42 },
            { id: 'test-namespace-2', amount_of_entities: 0 },
        ];

        (globalThis.fetch as any).mockResolvedValueOnce({
            ok: true,
            json: async () => mockNamespaces,
        });

        render(
            <BrowserRouter>
                <Namespaces />
            </BrowserRouter>
        );

        // Wait for the rows to render
        await waitFor(() => {
            expect(screen.getByText('test-namespace-1')).toBeInTheDocument();
        });

        // Verify the data is laid out
        expect(screen.getByText('42')).toBeInTheDocument();
        expect(screen.getByText('test-namespace-2')).toBeInTheDocument();
        expect(screen.getByText('0')).toBeInTheDocument();
    });

    it('displays empty state when no namespaces exist', async () => {
        (globalThis.fetch as any).mockResolvedValueOnce({
            ok: true,
            json: async () => [],
        });

        render(
            <BrowserRouter>
                <Namespaces />
            </BrowserRouter>
        );

        await waitFor(() => {
            expect(screen.getByText('No namespaces found. Create one to get started.')).toBeInTheDocument();
        });
    });
});

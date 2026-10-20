export type HealthResponse = { status: string };

export const api = {
  async health(): Promise<HealthResponse> {
    const response = await fetch("/api/health");
    if (!response.ok) {
      throw new Error(`Health check failed: ${response.status}`);
    }
    return response.json() as Promise<HealthResponse>;
  },
};

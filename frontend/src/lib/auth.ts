export const setToken = (token: string) => {
  if (typeof window !== "undefined") {
    localStorage.setItem("sentinel_token", token);
  }
};

export const getToken = (): string | null => {
  if (typeof window !== "undefined") {
    return localStorage.getItem("sentinel_token");
  }
  return null;
};

export const removeToken = () => {
  if (typeof window !== "undefined") {
    localStorage.removeItem("sentinel_token");
  }
};

export const isAuthenticated = () => {
  return !!getToken();
};

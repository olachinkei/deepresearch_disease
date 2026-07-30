import {
  index,
  route,
  type RouteConfig,
} from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("api/research", "routes/api.research.ts"),
  route("api/turns/:turnId/cancel", "routes/api.turns.$turnId.cancel.ts"),
  route("api/turns/:turnId/feedback", "routes/api.turns.$turnId.feedback.ts"),
] satisfies RouteConfig;

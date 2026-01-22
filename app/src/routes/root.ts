import { Router, type Request, type Response } from "express";

import { dashboardHtml } from "../views/dashboard.js";

export function createRootRouter(): Router {
  const router = Router();

  router.get("/", (_req: Request, res: Response) => {
    res.type("html");
    res.send(dashboardHtml);
  });

  return router;
}

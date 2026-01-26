import { Router, type Request, type Response } from "express";
import path from "node:path";

export function createRootRouter(): Router {
  const router = Router();

  router.get("/", (_req: Request, res: Response) => {
    res.redirect("http://localhost:5173");
  });

  router.get("/*", (_req: Request, res: Response) => {
    res.redirect("http://localhost:5173");
  });

  return router;
}

import { Router, type Request, type Response } from "express";
import path from "node:path";

export function createRootRouter(): Router {
  const router = Router();

  router.get("/", (_req: Request, res: Response) => {
    res.sendFile(path.resolve("public/index.html"));
  });

  router.get("/*", (_req: Request, res: Response) => {
    res.sendFile(path.resolve("public/index.html"));
  });

  return router;
}

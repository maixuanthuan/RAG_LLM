def rrf(rank_lists: dict[str, list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: hợp nhất nhiều danh sách xếp hạng, không cần cùng thang điểm."""
    scores: dict[str, float] = {}
    for ids in rank_lists.values():
        for rank, item in enumerate(ids, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

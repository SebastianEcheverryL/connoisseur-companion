import numpy as np

from connoisseur.retrieval.fusion import Hit, fuse, minmax, to_similarity


def test_minmax_normalises_to_unit_range():
    np.testing.assert_allclose(minmax([1.0, 3.0, 2.0]), [0.0, 1.0, 0.5])


def test_minmax_constant_and_empty():
    np.testing.assert_allclose(minmax([0.4, 0.4]), [1.0, 1.0])
    assert minmax([]).size == 0


def test_to_similarity_inverts_cosine_distance():
    np.testing.assert_allclose(to_similarity([0.0, 0.25]), [1.0, 0.75])


def test_fuse_merges_hits_that_share_a_key():
    text = [Hit("1", 0.9), Hit("2", 0.5)]
    images = [Hit("2", 0.8), Hit("3", 0.2)]
    ranked = fuse(text, images, w_text=0.6, w_img=0.4, top_n=None)
    scores = {r.key: r.fused for r in ranked}
    # "2" is weakest by text but strongest by image: 0.6*0 + 0.4*1
    assert scores == {"1": 0.6, "2": 0.4, "3": 0.0}
    assert [r.key for r in ranked] == ["1", "2", "3"]


def test_fuse_weights_can_favour_images():
    text = [Hit("a", 0.9), Hit("b", 0.1)]
    images = [Hit("b", 0.9), Hit("a", 0.1)]
    assert fuse(text, images, w_text=0.2, w_img=0.8, top_n=1)[0].key == "b"


def test_fuse_top_n_limits_results():
    assert len(fuse([Hit(str(i), i / 10) for i in range(5)], [], top_n=2)) == 2

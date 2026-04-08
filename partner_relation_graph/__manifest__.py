{
    "name": "Partner Relation Graph",
    "version": "18.0.1.0.9",
    "category": "Contacts",
    "summary": "Read-only relationship graph for partner_multi_relation",
    "author": "ECOnGOOD",
    "license": "AGPL-3",
    "depends": [
        "contacts",
        "web",
        "partner_multi_relation",
    ],
    "data": [
        "views/partner_relation_graph_views.xml",
        "views/res_partner_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "partner_relation_graph/static/lib/simple_force_graph.js",
            "partner_relation_graph/static/src/js/relation_graph_core.esm.js",
            "partner_relation_graph/static/src/js/relation_graph_field.esm.js",
            "partner_relation_graph/static/src/js/relation_graph_action.esm.js",
            "partner_relation_graph/static/src/xml/relation_graph.xml",
            "partner_relation_graph/static/src/scss/relation_graph.scss",
        ],
    },
    "installable": True,
    "application": False,
}
